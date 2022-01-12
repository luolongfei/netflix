#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Netflix

监听奈飞（netflix）密码变更邮件，自动重置密码。

流程：实时监听邮件，发现有人修改了密码 -> 访问奈飞，点击忘记密码 -> 等待接收奈飞的重置密码邮件 -> 收到重置密码邮件，访问邮件内的链接，
进行密码重置操作，恢复初始密码

@author mybsdc <mybsdc@gmail.com>
@date 2021/6/29
@time 11:20
"""

import os
import sys
import time
import argparse
import random
import string
import json
import re
import datetime
import traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.remote.webelement import WebElement
from dotenv import load_dotenv
from loguru import logger
import imaplib
import email
from email.header import decode_header
import redis
import ssl
import smtplib
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from email import encoders
from utils.version import __version__


def catch_exception(origin_func):
    """
    用于异常捕获的装饰器
    :param origin_func:
    :return:
    """

    def wrapper(*args, **kwargs):
        try:
            return origin_func(*args, **kwargs)
        except AssertionError as e:
            logger.error(f'参数错误：{str(e)}')
        except NoSuchElementException as e:
            logger.error('匹配元素超时，超过 {} 秒依然没有发现元素：{}', Netflix.TIMEOUT, str(e))
        except TimeoutException as e:
            logger.error(f'请求超时：{Netflix.driver.current_url} 异常：{str(e)}')
        except WebDriverException as e:
            logger.error(f'未知错误：{str(e)}')
        except Exception as e:
            logger.error('出错：{} 位置：{}', str(e), traceback.format_exc())
        finally:
            Netflix.driver.quit()
            logger.info('已关闭浏览器，释放资源占用')

    return wrapper


class Netflix(object):
    # 超时秒数，包括隐式等待和显式等待
    TIMEOUT = 24

    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'

    LOGIN_URL = 'https://www.netflix.com/login'
    RESET_PASSWORD_URL = 'https://www.netflix.com/password'
    FORGOT_PASSWORD_URL = 'https://www.netflix.com/LoginHelp'
    MANAGE_PROFILES_URL = 'https://www.netflix.com/ManageProfiles'

    # 请求重置密码的邮件正则
    RESET_MAIL_REGEX = re.compile(r'accountaccess.*?URL_ACCOUNT_ACCESS', re.I)

    # 提取完成密码重置的链接正则
    RESET_URL_REGEX = re.compile(r'https://www\.netflix\.com.*?URL_PASSWORD', re.I)

    # 密码被重置邮件正则
    PWD_HAS_BEEN_CHANGED_REGEX = re.compile(
        r'https?://.*?netflix\.com/YourAccount\?lnktrk=EMP&g=[^&]+&lkid=URL_YOUR_ACCOUNT_2', re.I)

    # 奈飞强迫用户修改密码
    FORCE_CHANGE_PASSWORD_REGEX = re.compile(r'https?://www\.netflix\.com/LoginHelp.*?lkid=URL_LOGIN_HELP', re.I)

    MAIL_SYMBOL_REGEX = re.compile('{(?!})|(?<!{)}')

    def __init__(self):
        Netflix.check_py_version()

        # 命令行参数
        self.args = self.get_all_args()

        # 加载环境变量
        if not os.path.exists('.env'):
            raise Exception('.env 文件不存在，请复制 .env.example 为 .env 文件')
        load_dotenv(verbose=True, override=True, encoding='utf-8')

        # 日志
        self.__logger_setting()

        self.options = webdriver.ChromeOptions()

        self.options.add_argument(f'user-agent={Netflix.USER_AGENT}')
        self.options.add_experimental_option('excludeSwitches', ['enable-automation'])
        self.options.add_experimental_option('useAutomationExtension', False)
        self.options.add_argument('--disable-extensions')  # 禁用扩展
        self.options.add_argument('--profile-directory=Default')
        self.options.add_argument('--incognito')  # 隐身模式
        self.options.add_argument('--disable-plugins-discovery')
        # self.options.add_argument('--start-maximized')
        self.options.add_argument('--window-size=1366,768')

        if self.args.headless or self.args.test:
            self.options.add_argument('--headless')  # 启用无头模式
        self.options.add_argument('--disable-gpu')  # 谷歌官方文档说加上此参数可减少 bug，仅适用于 Windows 系统

        # 解决 unknown error: DevToolsActivePort file doesn't exist
        self.options.add_argument('--no-sandbox')  # 绕过操作系统沙箱环境
        self.options.add_argument('--disable-dev-shm-usage')  # 解决资源限制，仅适用于 Linux 系统
        self.options.add_argument('--disable-blink-features=AutomationControlled')  # Chrome v88 以上版本正确隐藏浏览器特征

        self.driver = webdriver.Chrome(executable_path=os.getenv('DRIVER_EXECUTABLE_FILE'), options=self.options)
        self.driver.implicitly_wait(Netflix.TIMEOUT)

        # 防止通过 window.navigator.webdriver === true 检测模拟浏览器
        # 注意，低于 Chrome v88 （不含） 的浏览器可用此处代码隐藏 Web Driver 特征
        # 参考：
        # https://www.selenium.dev/selenium/docs/api/py/webdriver_chrome/selenium.webdriver.chrome.webdriver.html#selenium.webdriver.chrome.webdriver.WebDriver.execute_cdp_cmd
        # https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-addScriptToEvaluateOnNewDocument
        # self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        #     "source": """
        #         Object.defineProperty(navigator, 'webdriver', {
        #             get: () => undefined
        #         })
        #     """
        # })

        # 隐藏无头浏览器特征，增加检测难度
        with open('resources/stealth.min.js') as f:
            stealth_js = f.read()

            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': stealth_js
            })

        # 统配显式等待
        self.wait = WebDriverWait(self.driver, timeout=Netflix.TIMEOUT, poll_frequency=0.5)

        # 测试无头浏览器特征是否正确隐藏
        if self.args.test:
            logger.info('测试过程将只以无头模式进行')
            logger.info('开始测试无头浏览器特征是否正确隐藏')

            self.driver.get('https://bot.sannysoft.com/')

            time.sleep(3.5)

            filename = 'bot_test.png'
            self.__screenshot(filename, True)

            self.driver.quit()

            logger.info(f'已测试完成，测试结果保存在 {filename}')

            exit(0)

        self.BOT_MAIL_USERNAME = os.getenv('BOT_MAIL_USERNAME')
        assert self.BOT_MAIL_USERNAME, '请在 .env 文件配置 BOT_MAIL_USERNAME 的值，程式将监听此邮箱中的邮件内容'
        self.BOT_MAIL_PASSWORD = os.getenv('BOT_MAIL_PASSWORD')
        assert self.BOT_MAIL_PASSWORD, '请在 .env 文件配置 BOT_MAIL_PASSWORD 的值，程式用于登录被监听的邮箱'

        self.MULTIPLE_NETFLIX_ACCOUNTS = Netflix._parse_multiple_accounts()

        # 获取最近几天的邮件
        self.day = 3

        # 最多等待几分钟重置邮件的到来
        self.max_wait_reset_mail_time = 10

        # 恢复密码失败后最多重试几次
        self.max_num_of_attempts = 12

        self.first_time = []
        self.today = Netflix.today_()

        # 线程池
        self.max_workers = self.args.max_workers

        # Redis 配置
        self.REDIS_HOST = os.getenv('REDIS_HOST', '127.0.0.1')
        self.REDIS_PORT = os.getenv('REDIS_PORT', 6379)
        self.redis = None

    @staticmethod
    def _parse_multiple_accounts():
        accounts = os.getenv('MULTIPLE_NETFLIX_ACCOUNTS')

        match = re.findall(r'\[(?P<u>[^|\]]+?)\|(?P<p>[^|\]]+?)\|(?P<n>[^]]+?)\]', accounts, re.I)
        if match:
            return [{'u': item[0], 'p': item[1], 'n': item[2]} for item in match]

        raise Exception('未配置 Netflix 账户')

    @staticmethod
    def format_time(time: str or int, format: str = '%m/%d %H:%M:%S') -> str:
        return datetime.datetime.fromtimestamp(time).strftime(format)

    @staticmethod
    def today_():
        return str(datetime.date.today())

    def __logger_setting(self) -> None:
        logger.remove()

        level = 'DEBUG' if self.args.debug else 'INFO'
        format = '<green>[{time:YYYY-MM-DD HH:mm:ss.SSS}]</green> <b><level>{level: <8}</level></b> | <cyan>{process.id}</cyan>:<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>'

        logger.add('logs/{time:YYYY-MM-DD}.log', level=level, format=format, encoding='utf-8')
        logger.add(sys.stderr, colorize=True, level=level, format=format)

    @staticmethod
    def check_py_version(major=3, minor=7):
        if sys.version_info < (major, minor):
            raise UserWarning(f'请使用 python {major}.{minor} 及以上版本，推荐使用 python 3.9.8')

    @staticmethod
    def get_all_args():
        """
        获取所有命令行参数
        :return:
        """
        parser = argparse.ArgumentParser(description='Netflix 的各种参数及其含义', epilog='')
        parser.add_argument('-mw', '--max_workers', help='最大线程数', default=1, type=int)
        parser.add_argument('-d', '--debug', help='是否开启 Debug 模式', action='store_true')
        parser.add_argument('-f', '--force', help='是否强制执行，当然也要满足有“新的密码被重置的邮件”的条件', action='store_true')
        parser.add_argument('-t', '--test', help='测试无头浏览器特征是否正确隐藏', action='store_true')
        parser.add_argument('-hl', '--headless', help='是否启用无头模式', action='store_true')

        return parser.parse_args()

    def __login(self, netflix_username: str, netflix_password: str):
        """
        登录
        :param netflix_username:
        :param netflix_password:
        :return:
        """
        logger.debug('尝试登录账户：{}', netflix_username)

        # 多账户，每次登录前需要清除 cookies
        self.driver.delete_all_cookies()

        self.driver.get(Netflix.LOGIN_URL)

        u = self.driver.find_element_by_id('id_userLoginId')
        u.clear()
        u.send_keys(netflix_username)

        time.sleep(1.1)

        p = self.driver.find_element_by_id('id_password')
        p.clear()
        p.send_keys(netflix_password)

        self.driver.find_element_by_class_name('login-button').click()

        try:
            WebDriverWait(self.driver, timeout=3, poll_frequency=0.94).until(lambda d: 'browse' in d.current_url)
        except Exception as e:
            WebDriverWait(self.driver, timeout=2, poll_frequency=0.5).until(
                EC.visibility_of_element_located(
                    self.driver.find_element_by_xpath('//a[@data-uia="header-signout-link"]')),
                '查找登出元素未果')

            logger.warning(f'当前账户可能非 Netflix 会员，本次登录没有意义')

        logger.debug(f'已成功登录。当前地址为：{self.driver.current_url}')

        return True

    def __forgot_password(self, netflix_username: str):
        """
        忘记密码
        :param netflix_username:
        :return:
        """
        logger.info('尝试忘记密码')

        self.driver.delete_all_cookies()

        self.driver.get(Netflix.FORGOT_PASSWORD_URL)

        forgot_pwd = self.driver.find_element_by_id('forgot_password_input')
        forgot_pwd.clear()
        Netflix.send_keys_delay_random(forgot_pwd, netflix_username)

        time.sleep(1)

        self.handle_click_events(self.click_forgot_pwd_btn, max_num_of_attempts=12)

        # 直到页面显示已发送邮件
        logger.debug('检测是否已到送信完成画面')
        self.wait.until(EC.visibility_of(
            self.driver.find_element_by_xpath('//*[@class="login-content"]//h2[@data-uia="email_sent_label"]')),
            '查找送信完成元素未果')

        logger.info('已发送重置密码邮件到 {}，注意查收', netflix_username)

        return True

    def click_forgot_pwd_btn(self):
        """
        点击忘记密码按钮
        :return:
        """
        self.driver.find_element_by_class_name('forgot-password-action-button').click()

    def __reset_password(self, curr_netflix_password: str, new_netflix_password: str):
        """
        账户内修改密码
        :param curr_netflix_password:
        :param new_netflix_password:
        :return:
        """
        try:
            self.driver.get(Netflix.RESET_PASSWORD_URL)

            curr_pwd = self.driver.find_element_by_id('id_currentPassword')
            curr_pwd.clear()
            Netflix.send_keys_delay_random(curr_pwd, curr_netflix_password)

            time.sleep(1)

            new_pwd = self.driver.find_element_by_id('id_newPassword')
            new_pwd.clear()
            Netflix.send_keys_delay_random(new_pwd, new_netflix_password)

            time.sleep(1)

            confirm_new_pwd = self.driver.find_element_by_id('id_confirmNewPassword')
            confirm_new_pwd.clear()
            Netflix.send_keys_delay_random(confirm_new_pwd, new_netflix_password)

            time.sleep(1.1)

            # 其它设备无需重新登录
            self.driver.find_element_by_xpath('//li[@data-uia="field-requireAllDevicesSignIn+wrapper"]').click()

            time.sleep(1)

            self.handle_click_events(self.click_submit_btn)

            return self.__pwd_change_result()
        except Exception as e:
            raise Exception(f'直接在账户内修改密码出错：' + str(e))

    def input_pwd(self, new_netflix_password: str) -> None:
        """
        输入密码
        :param new_netflix_password:
        :return:
        """
        new_pwd = self.driver.find_element_by_id('id_newPassword')
        new_pwd.clear()
        Netflix.send_keys_delay_random(new_pwd, new_netflix_password)

        time.sleep(2)

        confirm_new_pwd = self.driver.find_element_by_id('id_confirmNewPassword')
        confirm_new_pwd.clear()
        Netflix.send_keys_delay_random(confirm_new_pwd, new_netflix_password)

        time.sleep(1)

    def click_submit_btn(self):
        """
        点击提交输入的密码
        :return:
        """
        self.driver.find_element_by_id('btn-save').click()

    def element_visibility_of(self, xpath: str, verify_val: bool = False,
                              max_num_of_attempts: int = 3, el_wait_time: int = 2) -> WebElement or None:
        """
        元素是否存在且可见
        适用于在已经加载完的网页做检测，可见且存在则返回元素，否则返回 None
        :param xpath:
        :param verify_val: 如果传入 True，则验证元素是否有值，或者 inner HTML 不为空，并作为关联条件
        :param max_num_of_attempts: 最大尝试次数，由于有的元素的值可能是异步加载的，需要多次尝试是否能获取到值，每次获取间隔休眠次数秒
        :param el_wait_time: 等待时间，查找元素最多等待多少秒，默认 2 秒
        :return:
        """
        try:
            self.driver.implicitly_wait(2)

            start_time = time.time()
            while True:
                if time.time() - start_time > el_wait_time:
                    return None

                try:
                    # 此处只为找到元素，如果下面不需要验证元素是否有值的话，则使用此处找到的元素
                    # 否则下面验值逻辑会重新找到该元素以使用，此处的 el 会被覆盖
                    el = self.driver.find_element_by_xpath(xpath)

                    break
                except Exception:
                    pass

            num = 0
            while True:
                if not verify_val:
                    break

                # 需要每次循环找到此元素，以确定元素的值是否发生变化
                el = self.driver.find_element_by_xpath(xpath)

                if el.tag_name == 'input':
                    val = el.get_attribute('value')
                    if val and len(val) > 0:
                        break
                elif el.text != '':
                    break

                # 多次尝试无果则放弃
                if num > max_num_of_attempts:
                    break
                num += 1

                time.sleep(num)

            return el if EC.visibility_of(el) else None
        except Exception:
            return None
        finally:
            self.driver.implicitly_wait(Netflix.TIMEOUT)

    def has_unknown_error_alert(self, error_el_xpath: str) -> bool:
        """
        页面提示未知错误
        :return:
        """
        error_tips_el = self.element_visibility_of(error_el_xpath, True)
        if error_tips_el:
            # 密码修改成功画面的提示语与错误提示语共用的同一个元素，防止误报
            if 'YourAccount?confirm=password' in self.driver.current_url or 'Your password has been changed' in error_tips_el.text:
                return False

            logger.warning(f'页面出现未知错误：{error_tips_el.text}')

            return True

        return False

    def handle_click_events(self, func, error_el_xpath='//div[@class="ui-message-contents"]',
                            max_num_of_attempts: int = 10):
        """
        处理点击事件

        在某些画面点击提交的时候，有可能报未知错误，需要稍等片刻再点击才正常
        :param func:
        :param max_num_of_attempts:
        :return:
        """
        func()

        num = 0
        while True:
            if self.has_unknown_error_alert(error_el_xpath):
                func()

                if num >= max_num_of_attempts:
                    raise Exception('处理未知错误失败')
                num += 1

                logger.info(f'程式将休眠 {num} 秒后重试点击动作')
                time.sleep(num)
            else:
                break

    def handle_unknown_error_alert(self, func, max_try: int = 10):
        """
        处理 Netflix 未知异常

        Netflix 会随机出现提醒页面出现未知错误，请稍后重试的情况，需要稍等片刻再点击才正常
        :param func:
        :param max_try:
        :return:
        """
        num_of_tries = 0

        while True:
            if self.has_unknown_error_alert():
                num_of_tries += 1

                sleep_time = num_of_tries * 2
                time.sleep(sleep_time)

                logger.info(f'程式将休眠 {sleep_time} 秒后重试点击')

                func()

                if num_of_tries >= max_try:
                    raise Exception('处理 Netflix 未知异常失败')
            else:
                break

    def __reset_password_via_mail(self, reset_url: str, new_netflix_password: str) -> bool:
        """
        通过邮件重置密码
        :param reset_url:
        :param new_netflix_password:
        :return:
        """
        logger.info('尝试通过邮件内的重置密码链接进行密码重置操作')

        self.driver.delete_all_cookies()

        self.driver.get(reset_url)

        self.input_pwd(new_netflix_password)

        self.handle_click_events(self.click_submit_btn)

        # 如果奈飞提示密码曾经用过，则应该先改为随机密码，然后再改回来
        pwd_error_tips = self.element_visibility_of('//div[@data-uia="field-newPassword+error"]')
        if pwd_error_tips:
            logger.warning('疑似 Netflix 提示你不能使用以前的密码（由于各种错误提示所在的 页面元素 相同，故无法准确判断，但是程式会妥善处理，不用担心）')
            logger.warning(f'原始的提示语为 {pwd_error_tips.text}，故程式将尝试先改为随机密码，然后再改回正常密码。')

            random_pwd = self.gen_random_pwd()
            self.input_pwd(random_pwd)

            self.handle_click_events(self.click_submit_btn)

            self.__pwd_change_result()

            # 账户内直接将密码改回原始值
            logger.info('尝试在账户内直接将密码改回原始密码')

            return self.__reset_password(random_pwd, new_netflix_password)

        return self.__pwd_change_result()

    def __pwd_change_result(self):
        """
        断言密码修改结果
        :return: 
        """
        try:
            self.wait.until(lambda d: d.current_url == 'https://www.netflix.com/YourAccount?confirm=password')

            logger.info('已成功修改密码')

            return True
        except Exception as e:
            raise Exception(f'未能正确跳到密码修改成功画面，疑似未成功，抛出异常：' + str(e))

    @staticmethod
    def parse_mail(data: bytes, onlySubject: bool = False) -> dict or str:
        """
        解析邮件内容
        :param data:
        :param onlySubject:
        :return:
        """
        resp = {
            'subject': '',
            'from': '',
            'date': '',
            'text': '',
            'html': ''
        }

        # 将字节邮件转换为一个 message 对象
        msg = email.message_from_bytes(data)

        # 解码邮件主题
        subject, encoding = decode_header(msg['Subject'])[0]
        if isinstance(subject, bytes):
            # 如果是字节类型，则解码为字符串
            subject = subject.decode(encoding)

        if onlySubject:
            return subject

        # 解码邮件发送者
        from_, encoding = decode_header(msg.get('From'))[0]
        if isinstance(from_, bytes):
            from_ = from_.decode(encoding)

        # 解码送信日期
        date, encoding = decode_header(msg.get('Date'))[0]
        if isinstance(date, bytes):
            date = date.decode(encoding)

        logger.debug(f'\nSubject: {subject}\nFrom: {from_}\nDate: {date}')

        resp['subject'] = subject
        resp['from'] = from_
        resp['date'] = date

        # 邮件可能有多个部分，比如可能有 html、纯文本、附件 三个部分
        if msg.is_multipart():
            # 遍历邮件的各部分
            for part in msg.walk():
                # 获取邮件内容类型
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition'))

                if 'attachment' in content_disposition:
                    # 附件，暂不处理
                    # filename = part.get_filename()
                    # if filename:
                    #     open(filename, 'wb').write(part.get_payload(decode=True))
                    continue

                try:
                    # 获取邮件正文
                    body = part.get_payload(decode=True).decode()
                except Exception as e:
                    continue

                if content_type == 'text/plain':
                    resp['text'] = body
                elif content_type == 'text/html':
                    resp['html'] = body
        else:
            content_type = msg.get_content_type()
            body = msg.get_payload(decode=True).decode()

            if content_type == 'text/plain':
                resp['text'] = body
            elif content_type == 'text/html':
                # 可以选择将 html 写入文件以便预览，此处暂且不处理，直接给内容
                resp['html'] = body

        return resp

    @staticmethod
    def is_password_reset_result(text: str) -> bool:
        """
        是否密码重置结果邮件
        :param text:
        :return:
        """
        return Netflix.PWD_HAS_BEEN_CHANGED_REGEX.search(text) is not None

    @staticmethod
    def is_password_reset_request(text: str):
        """
        是否请求重置密码的邮件
        :param text:
        :return:
        """
        return Netflix.RESET_MAIL_REGEX.search(text) is not None

    @staticmethod
    def is_force_change_password_request(text: str):
        """
        是否奈飞强迫修改密码的邮件
        :param text:
        :return:
        """
        return Netflix.FORCE_CHANGE_PASSWORD_REGEX.search(text) is not None

    def get_mail_last_id(self, netflix_account_email: str):
        """
        获取最新的邮件 ID
        :param netflix_account_email:
        :return:
        """
        key_last_id = f'{netflix_account_email}.last_id'
        last_id = self.redis.get(key_last_id) if self.redis.exists(key_last_id) else 0

        return last_id

    def set_mail_last_id(self, netflix_account_email: str, id: int) -> bool:
        """
        设置最新的邮件 ID
        :param netflix_account_email:
        :param id:
        :return:
        """
        key_last_id = f'{netflix_account_email}.last_id'
        self.redis.set(key_last_id, id)

        return True

    def is_need_to_do(self, netflix_account_email: str) -> int:
        """
        是否需要做处理
        :param netflix_account_email:
        :return:
        """
        key_need_to_do = f'{netflix_account_email}.need_to_do'
        need_to_do = self.redis.get(key_need_to_do) if self.redis.exists(key_need_to_do) else 1

        return need_to_do

    def set_need_to_do(self, netflix_account_email: str, status: int = 1) -> bool:
        """
        设置是否需要做处理
        :param netflix_account_email:
        :param status: 1：需要 0：不需要
        :return:
        """
        key_need_to_do = f'{netflix_account_email}.need_to_do'
        self.redis.set(key_need_to_do, status)

        return True

    def __fetch_mail(self, netflix_account_email: str, onlySubject: bool = False) -> str or None:
        """
        拉取邮件
        :param netflix_account_email:
        :param onlySubject:
        :return:
        """
        logger.debug('尝试拉取最新邮件，以监听是否有重置密码相关的邮件')

        with imaplib.IMAP4_SSL('imap.gmail.com', 993) as M:
            M.login(self.BOT_MAIL_USERNAME, self.BOT_MAIL_PASSWORD)
            status, total = M.select('INBOX', readonly=True)  # readonly=True 则邮件将不会被标记为已读

            # https://gist.github.com/martinrusev/6121028
            # https://stackoverflow.com/questions/5621341/search-before-after-with-pythons-imaplib
            after_date = (datetime.date.today() - datetime.timedelta(self.day)).strftime(
                '%d-%b-%Y')  # 仅需要最近 N 天的邮件，%b 表示字符月份
            criteria = f'(TO "<{netflix_account_email}>" SENTSINCE "{after_date}")'
            status, data = M.search(None, criteria)
            if status != 'OK':
                raise Exception('通过发信人以及送信时间过滤邮件时出错')

            last_id = self.get_mail_last_id(netflix_account_email)
            data = data[0].split()[::-1]
            for num in data:
                id = int(num)
                if id <= last_id:  # 只要最新未读的
                    continue

                status, mail_data = M.fetch(num, '(RFC822)')
                if status != 'OK':
                    logger.error(f'邮箱 {self.BOT_MAIL_USERNAME} 在为 {netflix_account_email} 拉取 ID 为 {id} 的邮件时出错')

                    continue

                # 解析邮件
                resp = Netflix.parse_mail(mail_data[0][1], onlySubject)

                # 记录邮件 ID，之后此邮箱的此类型邮件必须大于此 ID 才有效，且此 ID 跟随 Netflix 账户
                self.set_mail_last_id(netflix_account_email, id)

                return resp

        return None

    def pwd_result_mail_listener(self, netflix_account_email: str):
        """
        监听密码重置结果邮件
        既可能是恶意用户，也可能是 Netflix 强迫用户重置密码而发来的邮件，借此触发我们后续流程
        :param netflix_account_email:
        :return:
        """
        # 拉取最新邮件
        resp = self.__fetch_mail(netflix_account_email)
        if not resp:
            return None

        # 定义事件类型 0：未知 1：用户恶意修改密码 2：Netflix 强迫用户修改密码
        event_type = 0

        if Netflix.is_password_reset_result(resp['text']):  # 检测到有用户恶意修改密码
            logger.info('检测到有人修改了 Netflix 账户 {} 的密码', netflix_account_email)

            event_type = 1
            need_to_do = self.is_need_to_do(netflix_account_email)
            if not need_to_do:
                logger.info('今次检测到的密码重置结果邮件应是脚本的动作回执，故不做处理')

                self.set_need_to_do(netflix_account_email, 1)

                return None

            # 处理首次运行程式的情形
            if netflix_account_email not in self.first_time:
                self.first_time.append(netflix_account_email)

                if self.args.force:
                    logger.info(f'强制运行，检测到账户 {netflix_account_email} 存在密码被重置的邮件，已触发密码重置流程')

                    return True, event_type

                logger.info(f'首次运行，故今次检测账户 {netflix_account_email}，发现的都是一些旧的密码被重置的邮件，不做处理')

                return None

            return True, event_type
        elif Netflix.is_force_change_password_request(resp['text']):  # 检测到奈飞强迫用户修改密码
            logger.info('检测到 Netflix 以安全起见，强迫用户修改账户 {} 的密码', netflix_account_email)

            event_type = 2

            return True, event_type

    def pwd_reset_request_mail_listener(self, netflix_account_email) -> str or None:
        """
        监听请求重置密码的邮件
        在发起重置密码动作后，我们会收到 Netflix 的邮件
        :param netflix_account_email:
        :return:
        """
        # 拉取最新邮件
        resp = self.__fetch_mail(netflix_account_email)

        if self.is_password_reset_request(resp['text']):
            logger.info('Netflix 账户 {} 已收到请求重置密码的邮件，开始提取重置链接', netflix_account_email)

            match = Netflix.RESET_URL_REGEX.search(resp['text'])
            if not match:
                raise Exception('已命中重置密码邮件，但是未能正确提取重置密码链接，请调查一下')

            logger.info('已成功提取重置密码链接')
            logger.info(f'本次重置链接为：{match.group(0)}')

            return match.group(0)

        return None

    @staticmethod
    def time_diff(start_time, end_time):
        """
        计算时间间隔
        :param start_time: 开始时间戳
        :param end_time: 结束时间戳
        :return:
        """
        diff_time = end_time - start_time

        if diff_time < 0:
            raise ValueError('结束时间必须大于等于开始时间')

        if diff_time < 1:
            return '{:.2f}秒'.format(diff_time)
        else:
            diff_time = int(diff_time)

        if diff_time < 60:
            return '{:02d}秒'.format(diff_time)
        elif 60 <= diff_time < 3600:
            m, s = divmod(diff_time, 60)

            return '{:02d}分钟{:02d}秒'.format(m, s)
        elif 3600 <= diff_time < 24 * 3600:
            m, s = divmod(diff_time, 60)
            h, m = divmod(m, 60)

            return '{:02d}小时{:02d}分钟{:02d}秒'.format(h, m, s)
        elif 24 * 3600 <= diff_time:
            m, s = divmod(diff_time, 60)
            h, m = divmod(m, 60)
            d, h = divmod(h, 24)

            return '{:02d}天{:02d}小时{:02d}分钟{:02d}秒'.format(d, h, m, s)

    def __do_reset(self, netflix_account_email: str, p: str) -> bool:
        """
        执行重置密码流程
        :param netflix_account_email:
        :param p:
        :return:
        """
        self.__forgot_password(netflix_account_email)

        logger.info('等待接收重置密码链接')

        # 坐等奈飞发送的重置密码链接
        wait_start_time = time.time()

        while True:
            reset_link = self.pwd_reset_request_mail_listener(netflix_account_email)

            if reset_link:
                self.set_need_to_do(netflix_account_email, 0)  # 忽略下一封密码重置邮件

                break

            if (time.time() - wait_start_time) > 60 * self.max_wait_reset_mail_time:
                raise Exception(f'等待超过 {self.max_wait_reset_mail_time} 分钟，依然没有收到奈飞的重置密码来信，故将重走恢复密码流程')

            time.sleep(2)

        return self.__reset_password_via_mail(reset_link, p)

    @staticmethod
    def now(format='%Y-%m-%d %H:%M:%S.%f'):
        """
        当前时间
        精确到毫秒
        :return:
        """
        now = datetime.datetime.now().strftime(format)

        return now[:-3] if '%f' in format else now

    def __screenshot(self, filename: str, full_page=False):
        """
        截图
        :param filename:
        :param full_page: 仅无头模式支持截取全屏
        :return:
        """
        dir = os.path.dirname(filename)
        if dir and not os.path.exists(dir):
            os.makedirs(dir)

        if full_page:
            if not self.args.headless and not self.args.test:  # 若跟上 -t 参数则默认使用无头模式，可不传 -hl
                raise Exception('仅无头模式支持全屏截图，请跟上 -hl 参数后重试')

            original_size = self.driver.get_window_size()
            required_width = self.driver.execute_script('return document.body.parentNode.scrollWidth')
            required_height = self.driver.execute_script('return document.body.parentNode.scrollHeight')

            self.driver.set_window_size(required_width, required_height)

            self.driver.find_element_by_tag_name('body').screenshot(filename)  # 通过 body 元素截图可隐藏滚动条

            self.driver.set_window_size(original_size['width'], original_size['height'])

            return True

        self.driver.save_screenshot(filename)

        return True

    @staticmethod
    def symbol_replace(val):
        """
        转义花括符
        :param val:
        :return:
        """
        real_val = val.group()
        if real_val == '{':
            return '{{'
        elif real_val == '}':
            return '}}'
        else:
            return ''

    @staticmethod
    def send_mail(subject: str, content: str or list, to: str = None, files: list = [], text_plain: str = '',
                  template='default') -> bool:
        """
        发送邮件
        :param subject:
        :param content:
        :param to:
        :param files:
        :param text_plain: 纯文本，可选
        :param template:
        :return:
        """
        try:
            if not to:
                to = os.getenv('INBOX')
                assert to, '尚未在 .env 文件中检测到 INBOX 的值，请配置之'

            # 发信邮箱账户
            username = os.getenv('BOT_MAIL_USERNAME')
            password = os.getenv('BOT_MAIL_PASSWORD')

            # 根据发信邮箱类型自动使用合适的配置
            if '@gmail.com' in username:
                host = 'smtp.gmail.com'
                secure = 'tls'
                port = 587
            elif '@qq.com' in username:
                host = 'smtp.qq.com'
                secure = 'tls'
                port = 587
            elif '@163.com' in username:
                host = 'smtp.163.com'
                secure = 'ssl'
                port = 465
            else:
                raise ValueError(f'「{username}」 是不受支持的邮箱。目前仅支持谷歌邮箱、QQ邮箱以及163邮箱，推荐使用谷歌邮箱。')

            # 格式化邮件内容
            if isinstance(content, list):
                with open('./mail/{}.html'.format(template), 'r', encoding='utf-8') as f:
                    template_content = f.read()
                    text = Netflix.MAIL_SYMBOL_REGEX.sub(Netflix.symbol_replace, template_content).format(*content)
                    real_content = text.replace('{{', '{').replace('}}', '}')
            elif isinstance(content, str):
                real_content = content
            else:
                raise TypeError(f'邮件内容类型仅支持 list 或 str，当前传入的类型为 {type(content)}')

            # 邮件内容设置多个部分
            msg = MIMEMultipart('alternative')

            msg['From'] = formataddr(('Im Robot', username))
            msg['To'] = formataddr(('', to))
            msg['Subject'] = subject

            # 添加纯文本内容（针对不支持 html 的邮件客户端）
            # 注意：当同时包含纯文本和 html 时，一定要先添加纯文本再添加 html，因为一般邮件客户端默认优先展示最后添加的部分
            # https://realpython.com/python-send-email/
            # https://docs.python.org/3/library/email.mime.html
            # As not all email clients display HTML content by default, and some people choose only to receive plain-text emails for security reasons,
            # it is important to include a plain-text alternative for HTML messages. As the email client will render the last multipart attachment first,
            # make sure to add the HTML message after the plain-text version.
            if text_plain:
                msg.attach(MIMEText(text_plain, 'plain', 'utf-8'))
            elif isinstance(content, str):  # 仅当传入内容是纯文本才添加纯文本内容，因为一般传入 list 的情况下，我只想发送 html 内容
                text_plain = MIMEText(content, 'plain', 'utf-8')
                msg.attach(text_plain)

            # 添加网页
            page = MIMEText(real_content, 'html', 'utf-8')
            msg.attach(page)

            # 添加 html 内联图片，仅适配模板中头像
            if isinstance(content, list):
                with open('mail/images/ting.jpg', 'rb') as img:
                    avatar = MIMEImage(img.read())
                    avatar.add_header('Content-ID', '<avatar>')
                    msg.attach(avatar)

            # 添加附件
            for path in files:  # 注意，如果文件尺寸为 0 会被忽略
                if not os.path.exists(path):
                    logger.error(f'发送邮件时，发现要添加的附件（{path}）不存在，本次已忽略此附件')

                    continue

                part = MIMEBase('application', 'octet-stream')
                with open(path, 'rb') as file:
                    part.set_payload(file.read())

                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment; filename="{}"'.format(Path(path).name))
                msg.attach(part)

            with smtplib.SMTP_SSL(host=host, port=port) if secure == 'ssl' else smtplib.SMTP(host=host,
                                                                                             port=port) as server:
                # 启用 tls 加密，优于 ssl
                if secure == 'tls':
                    server.starttls(context=ssl.create_default_context())

                server.login(username, password)
                server.sendmail(from_addr=username, to_addrs=to, msg=msg.as_string())

                return True
        except Exception as e:
            logger.error('邮件送信失败：' + str(e))

            return False

    @staticmethod
    def gen_random_pwd(length: int = 13):
        """
        生成随机密码
        :param length:
        :return:
        """
        characters = string.ascii_letters + string.digits  # + string.punctuation
        password = ''.join(random.choice(characters) for i in range(length))

        return password

    @staticmethod
    def send_keys_delay_random(element, keys, min_delay=0.11, max_delay=0.24):
        """
        随机延迟输入
        :param element:
        :param keys:
        :param min_delay:
        :param max_delay:
        :return:
        """
        for key in keys:
            element.send_keys(key)
            time.sleep(random.uniform(min_delay, max_delay))

    def error_page_screenshot(self) -> str:
        """
        错误画面实时截图
        :return:
        """
        screenshot_file = f'logs/screenshots/error_page/{self.now("%Y-%m-%d_%H_%M_%S_%f")}.png'

        self.__screenshot(screenshot_file)

        logger.info(f'出错画面已被截图，图片文件保存在：{screenshot_file}')

        return screenshot_file

    @staticmethod
    def get_event_reason(event_type: int) -> str:
        if event_type == 1:
            return '用户恶意修改密码'
        elif event_type == 2:
            return 'Netflix 强迫用户修改密码'

        return '未知原因'

    def __recover_name(self, link_el: WebElement, real_name: str) -> bool:
        """
        执行用户名恢复操作
        :param link_el:
        :param real_name:
        :return:
        """
        try:
            link_el.click()

            WebDriverWait(self.driver, timeout=4.2, poll_frequency=0.94).until(
                EC.visibility_of_element_located((By.XPATH, '//button[@data-uia="profile-save-button"]')),
                '保存按钮不可点击')

            name_input_el = self.driver.find_element_by_id('profile-name-entry')
            name_input_el.clear()
            name_input_el.send_keys(real_name)

            self.driver.find_element_by_xpath('//button[@data-uia="profile-save-button"]').click()

            WebDriverWait(self.driver, timeout=5, poll_frequency=0.94).until(
                EC.visibility_of(self.driver.find_element_by_class_name('profile-link')), '编辑按钮元素不可见')

            return True
        except Exception as e:
            logger.error(f'尝试恢复用户名出错：{str(e)}')

            return False

    def protect_account_name(self):
        """
        保护账户名不被修改
        :return:
        """
        for item in self.MULTIPLE_NETFLIX_ACCOUNTS:
            try:
                self.__login(item.get('u'), item.get('p'))

                self.driver.get(Netflix.MANAGE_PROFILES_URL)

                WebDriverWait(self.driver, timeout=3, poll_frequency=0.94).until(
                    lambda d: 'ManageProfiles' in d.current_url,
                    f'{item.get("u")} 可能非会员，无法访问 {Netflix.MANAGE_PROFILES_URL} 地址')

                # 五个子账户，逐个检查
                success_num = 0
                events_count = 0
                for index in range(5):
                    real_name = item.get('n') + f'_0{index + 1}'

                    link_el = self.driver.find_elements_by_xpath('//a[@class="profile-link"]')[index]
                    curr_name = link_el.text

                    if curr_name != real_name:
                        logger.info(f'发现用户名被篡改为 【{curr_name}】')
                        events_count += 1

                        if self.__recover_name(link_el, real_name):
                            logger.success(f'程式已将 【{curr_name}】 恢复为 【{real_name}】')

                            # 成功处理一件，就记录一件
                            success_num += 1

                if success_num:
                    self.driver.find_element_by_xpath('//span[@data-uia="profile-button"]').click()

                    WebDriverWait(self.driver, timeout=3, poll_frequency=0.94).until(
                        lambda d: 'browse' in d.current_url)

                    logger.success(f'用户名已恢复完成，共 {events_count} 件篡改事件，已成功处理 {success_num} 件')

                logger.debug('用户名处理结束')
            except Exception as e:
                logger.warning(f'用户名篡改检测出错：{str(e)} [账户：{item.get("u")}]')

    @catch_exception
    def run(self):
        logger.info('当前程序版本为 ' + __version__)
        logger.info('开始监听密码被改邮件')

        # 监听密码被改邮件
        last_protection_time = time.time()
        while True:
            real_today = Netflix.today_()
            if self.today != real_today:
                self.today = real_today
                self.__logger_setting()

            self.redis = redis.Redis(host=self.REDIS_HOST, port=self.REDIS_PORT, db=0, decode_responses=True)
            self.redis.set_response_callback('GET', int)

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                all_tasks = {
                    executor.submit(self.pwd_result_mail_listener, item.get('u')): (item.get('u'), item.get('p')) for
                    item in
                    self.MULTIPLE_NETFLIX_ACCOUNTS}

                for future in as_completed(all_tasks):
                    try:
                        u, p = all_tasks[future]

                        result = future.result()
                        if not result:
                            continue

                        data, event_type = result
                        event_reason = Netflix.get_event_reason(event_type)
                        start_time = time.time()

                        num = 1
                        while True:
                            try:
                                if event_type == 1:  # 用户恶意修改密码
                                    self.__do_reset(u, p)  # 要么返回 True，要么抛异常

                                    logger.success('成功恢复原始密码')
                                    Netflix.send_mail(
                                        f'在 {Netflix.format_time(start_time)} 发现有人修改了 Netflix 账户 {u} 的密码，我已自动将密码恢复为初始状态',
                                        [
                                            f'程式在 {self.now()} 已将密码恢复为初始状态，共耗时{Netflix.time_diff(start_time, time.time())}，本次自动处理成功。'])

                                    self.set_need_to_do(u, 0)

                                    break
                                elif event_type == 2:  # Netflix 强迫用户修改密码
                                    # 重置为随机密码
                                    logger.info('尝试先修改为随机密码')
                                    random_pwd = Netflix.gen_random_pwd(8)
                                    self.__do_reset(u, random_pwd)

                                    # 账户内自动修改为原始密码
                                    self.__reset_password(random_pwd, p)

                                    self.set_need_to_do(u, 0)

                                    logger.success('成功从随机密码改回原始密码')
                                    Netflix.send_mail(
                                        f'在 {Netflix.format_time(start_time)} 发现 Netflix 强迫您修改账户 {u} 的密码，我已自动将密码恢复为初始状态',
                                        [
                                            f'程式在 {self.now()} 已将密码恢复为初始状态，共耗时{Netflix.time_diff(start_time, time.time())}，本次自动处理成功。'])

                                    break
                            except Exception as e:
                                logger.warning(
                                    f'在执行密码恢复操作过程中出错：{str(e)}，将重试，最多不超过 {self.max_num_of_attempts} 次 [{num}/{self.max_num_of_attempts}]')
                                self.error_page_screenshot()
                            finally:
                                # 超过最大尝试次数
                                if num >= self.max_num_of_attempts:
                                    logger.error('重试失败次数过多，已放弃本次恢复密码动作，将继续监听新的密码事件')
                                    self.set_need_to_do(u, 1)  # 恢复检测

                                    Netflix.send_mail(f'主人，抱歉没能恢复 {u} 的密码，请尝试手动恢复', [
                                        f'今次触发恢复密码的动作的原因为：{event_reason}。<br>发现时间：{Netflix.format_time(start_time)}<br><br>程式一共尝试了 {num} 次恢复密码，均以失败告终。我已将今天的日志以及这次出错画面的截图作为附件发送给您，请查收。'],
                                                      files=[f'logs/{Netflix.now("%Y-%m-%d")}.log',
                                                             self.error_page_screenshot()])

                                    break

                                num += 1
                    except Exception as e:
                        logger.error('出错：{}', str(e))

            time.sleep(3)

            # 保护账户用户名
            if int(os.getenv('ENABLE_ACCOUNT_NAME_PROTECTION', 0)):
                now = time.time()
                if now - last_protection_time >= 124:
                    last_protection_time = now
                    self.protect_account_name()

            logger.debug('开始下一轮监听')


if __name__ == '__main__':
    Netflix = Netflix()
    Netflix.run()
