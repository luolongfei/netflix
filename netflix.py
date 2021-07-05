#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Netflix

监听奈飞（netflix）密码变更邮件，自动重置密码。

流程：实时监听邮件，发现有人修改了密码 -> 访问奈飞，点击忘记密码 -> 等待接收奈飞的重置密码邮件 -> 收到重置密码邮件，访问邮件内的链接，
进行密码重置操作，使用随机密码 -> 修改后回到正常的密码修改页面，将密码再次修改为原始值

@author mybsdc <mybsdc@gmail.com>
@date 2021/6/29
@time 11:20
"""

import os
import sys
import time
import argparse
import random
import json
import re
import datetime
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from dotenv import load_dotenv
from loguru import logger
import imaplib
import email
from email.header import decode_header
import redis
import ssl
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr


def catch_exception(origin_func):
    def wrapper(self, *args, **kwargs):
        """
        用于异常捕获的装饰器
        :param self:
        :param args:
        :param kwargs:
        :return:
        """
        try:
            return origin_func(self, *args, **kwargs)
        except AssertionError as e:
            logger.error(f'参数错误：{str(e)}')
        except NoSuchElementException as e:
            logger.error('匹配元素超时，超过 {} 秒依然没有发现元素：{}', Netflix.TIMEOUT, str(e))
        except TimeoutException as e:
            logger.error(f'请求超时：{self.driver.current_url} 异常：{str(e)}')
        except WebDriverException as e:
            logger.error(f'未知错误：{str(e)}')
        except Exception as e:
            logger.error('出错：{} 位置：{}', str(e), traceback.format_exc())
        finally:
            self.driver.quit()
            logger.info('已关闭浏览器，释放资源占用')

    return wrapper


class Netflix(object):
    # 超时秒数，包括隐式等待和显式等待
    TIMEOUT = 23

    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'

    LOGIN_URL = 'https://www.netflix.com/login'
    RESET_PASSWORD_URL = 'https://www.netflix.com/password'
    FORGOT_PASSWORD_URL = 'https://www.netflix.com/LoginHelp'

    RESET_MAIL_REGEX = re.compile(r'accountaccess.*?URL_ACCOUNT_ACCESS', re.I)
    RESET_URL_REGEX = re.compile(r'https://www\.netflix\.com.*?URL_PASSWORD', re.I)

    MAIL_SYMBOL_REGEX = re.compile('{(?!})|(?<!{)}')

    def __init__(self):
        Netflix.check_py_version()

        # 命令行参数
        self.args = self.get_all_args()

        # 加载环境变量
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

        # self.options.add_argument('--headless')  # 启用无头模式
        self.options.add_argument('--disable-gpu')  # 谷歌官方文档说加上此参数可减少 bug，仅适用于 Windows 系统

        # 解决 unknown error: DevToolsActivePort file doesn't exist
        self.options.add_argument('--no-sandbox')  # 绕过操作系统沙箱环境
        self.options.add_argument('--disable-dev-shm-usage')  # 解决资源限制，仅适用于 Linux 系统
        self.options.add_argument('--disable-blink-features=AutomationControlled')  # Chrome v88 以上版本正确隐藏浏览器特征

        self.driver = webdriver.Chrome(executable_path=os.getenv('DRIVER_EXECUTABLE_FILE'), options=self.options)
        self.driver.implicitly_wait(Netflix.TIMEOUT)

        # 防止通过 window.navigator.webdriver === true 检测模拟浏览器
        # 参考：
        # https://www.selenium.dev/selenium/docs/api/py/webdriver_chrome/selenium.webdriver.chrome.webdriver.html#selenium.webdriver.chrome.webdriver.WebDriver.execute_cdp_cmd
        # https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-addScriptToEvaluateOnNewDocument
        self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })

        # 隐藏无头浏览器特征，增加检测难度
        with open('resources/stealth.min.js') as f:
            stealth_js = f.read()

            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': stealth_js
            })

        # 统配显式等待
        self.wait = WebDriverWait(self.driver, timeout=Netflix.TIMEOUT, poll_frequency=0.5)

        self.BOT_MAIL_USERNAME = os.getenv('BOT_MAIL_USERNAME')
        self.BOT_MAIL_PASSWORD = os.getenv('BOT_MAIL_PASSWORD')

        self.MULTIPLE_NETFLIX_ACCOUNTS = Netflix._parse_multiple_accounts()

        # 获取最近几天的邮件
        self.day = 3

        # 最多等待几分钟重置邮件的到来
        self.max_wait_reset_mail_time = 10

        # 恢复密码失败后最多重试几次
        self.max_retry = 5

        self.first_time = []
        self.today = Netflix.today_()

        # 线程池
        self.max_workers = self.args.max_workers

        self.redis = None

    @staticmethod
    def _parse_multiple_accounts():
        accounts = os.getenv('MULTIPLE_NETFLIX_ACCOUNTS')

        match = re.findall(r'\[(?P<u>.*?)\|(?P<p>.*?)\]', accounts, re.I)
        if match:
            return [{'u': item[0], 'p': item[1]} for item in match]

        raise Exception('未配置 Netflix 账户')

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
    def check_py_version(major=3, minor=6):
        if sys.version_info < (major, minor):
            raise UserWarning(f'请使用 python {major}.{minor} 及以上版本，推荐使用 python 3.8.2')

    @staticmethod
    def get_all_args():
        """
        获取所有命令行参数
        :return:
        """
        parser = argparse.ArgumentParser(description='Netflix 的各种参数及其含义', epilog='')
        parser.add_argument('-mw', '--max_workers', help='最大线程数', default=1, type=int)
        parser.add_argument('-d', '--debug', help='是否开启 Debug 模式', action='store_true')
        parser.add_argument('-f', '--force', help='是否强制执行', action='store_true')

        return parser.parse_args()

    def __login(self, netflix_username: str, netflix_password: str):
        """
        登录
        :param netflix_username:
        :param netflix_password:
        :return:
        """
        logger.info('尝试登录 Netflix')

        # 多账户，每次登录前需要清除 cookies
        self.driver.delete_all_cookies()

        self.driver.get(Netflix.LOGIN_URL)

        u = self.driver.find_element_by_id('id_userLoginId')
        u.clear()
        Netflix.send_keys_delay_random(u, netflix_username)

        time.sleep(2)

        p = self.driver.find_element_by_id('id_password')
        p.clear()
        Netflix.send_keys_delay_random(p, netflix_password)

        self.driver.find_element_by_class_name('login-button').click()

        logger.debug(f'当前地址为：{self.driver.current_url}')

        self.wait.until(lambda d: d.current_url == 'https://www.netflix.com/browse')

        logger.info(f'已成功登录。当前地址为：{self.driver.current_url}')

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

        time.sleep(2)

        self.driver.find_element_by_class_name('forgot-password-action-button').click()

        # 直到页面显示已发送邮件
        self.wait.until(EC.visibility_of(
            self.driver.find_element_by_xpath('//*[@class="login-content"]//h2[@data-uia="email_sent_label"]')))

        logger.info('已发送重置密码邮件到 {}，注意查收', netflix_username)

        return True

    def __reset_password(self, curr_netflix_password: str, new_netflix_password: str):
        """
        重置密码
        :param curr_netflix_password:
        :param new_netflix_password:
        :return:
        """
        logger.info('尝试重置密码')

        self.driver.get(Netflix.RESET_PASSWORD_URL)

        curr_pwd = self.driver.find_element_by_id('id_currentPassword')
        curr_pwd.clear()
        Netflix.send_keys_delay_random(curr_pwd, curr_netflix_password)

        time.sleep(2)

        new_pwd = self.driver.find_element_by_id('id_newPassword')
        new_pwd.clear()
        Netflix.send_keys_delay_random(new_pwd, new_netflix_password)

        time.sleep(2)

        confirm_new_pwd = self.driver.find_element_by_id('id_confirmNewPassword')
        confirm_new_pwd.clear()
        Netflix.send_keys_delay_random(confirm_new_pwd, new_netflix_password)

        time.sleep(1.5)

        # 其它设备无需重新登录
        self.driver.find_element_by_xpath('//li[@data-uia="field-requireAllDevicesSignIn+wrapper"]').click()

        time.sleep(1)

        self.driver.find_element_by_id('btn-save').click()

        self.wait.until(lambda d: d.current_url == 'https://www.netflix.com/YourAccount?confirm=password')

        logger.info('密码已修改成功')

        return True

    def __reset_password_via_mail(self, reset_url: str, new_netflix_password: str):
        """
        通过邮件重置密码
        :param reset_url:
        :param new_netflix_password:
        :return:
        """
        logger.info('尝试通过邮件内的重置密码链接进行密码重置操作')

        self.driver.delete_all_cookies()

        self.driver.get(reset_url)

        new_pwd = self.driver.find_element_by_id('id_newPassword')
        new_pwd.clear()
        Netflix.send_keys_delay_random(new_pwd, new_netflix_password)

        time.sleep(2)

        confirm_new_pwd = self.driver.find_element_by_id('id_confirmNewPassword')
        confirm_new_pwd.clear()
        Netflix.send_keys_delay_random(confirm_new_pwd, new_netflix_password)

        time.sleep(1)

        self.driver.find_element_by_id('btn-save').click()

        self.wait.until(lambda d: d.current_url == 'https://www.netflix.com/YourAccount?confirm=password')

        logger.info('通过邮件内链接修改密码成功')

        return True

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
    def is_password_reset_result(subject: str) -> bool:
        """
        是否密码重置结果邮件
        :param subject:
        :return:
        """
        return subject in (
            '密碼已更改',
            'Your password has been changed',
            '您的密码已更改',
            'パスワード更新のご案内',
            '비밀번호 변경 알림',
            'Sandi sudah diubah',
            'Kata laluan anda telah ditukar',
            'Din adgangskode er blevet &aelig_ndret',
            'Ihr Passwort wurde ge&auml_ndert',
            'Tu contrase&ntilde_a se ha cambiado',
            'Mot de passe modifi&eacute_',
            'Tvoja je lozinka promijenjena',
            'La tua password &egrave_ stata modificata',
            'A jelszavad m&oacute_dosult',
            'Ditt l&ouml_senord har &auml_ndrats',
            'Mật khẩu của bạn đ&atilde_ thay đổi',
            'Parolanız değiştirildi',
            'Vaše heslo bylo změněno',
            'Ο κωδικός πρόσβασής σας άλλαξε',
            'Ваш пароль изменен',
            'הסיסמה שלך שונתה',
            'لقد تمّ تغيير كلمة المرور الخاصة بك.',
            'आपका पासवर्ड बदल दिया गया है',
        )

    @staticmethod
    def is_password_reset_request(text: str):
        """
        是否请求重置密码的邮件
        :param text:
        :return:
        """
        return Netflix.RESET_MAIL_REGEX.search(text) is not None

    def __fetch_mail(self, netflix_account_email: str, mail_type: int = 0) -> str or None:
        """
        拉取邮件
        :param netflix_account_email:
        :param mail_type: 支持传入 0 或 1，0 表示密码重置结果邮件，1 表示请求重置密码邮件
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

            key_last_id = f'{netflix_account_email}.last_id'
            last_id = self.redis.get(key_last_id) if self.redis.exists(key_last_id) else 0

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
                resp = Netflix.parse_mail(mail_data[0][1], mail_type == 0)

                if mail_type == 0:
                    # 检测到有人修改了密码
                    if Netflix.is_password_reset_result(resp):
                        logger.info('检测到有人修改了 Netflix 账户 {} 的密码', netflix_account_email)

                        # 记录邮件 ID，之后此邮箱的此类型邮件必须大于此 ID 才有效
                        self.redis.set(key_last_id, id)

                        key_need_to_do = f'{netflix_account_email}.need_to_do'
                        need_to_do = self.redis.get(key_need_to_do) if self.redis.exists(key_need_to_do) else 1

                        if not need_to_do:
                            logger.info('今次检测到的密码重置结果邮件应是脚本的动作回执，故不做处理')

                            self.redis.set(key_need_to_do, 1)

                            return None

                        if netflix_account_email not in self.first_time:
                            self.first_time.append(netflix_account_email)

                            if self.args.force:
                                logger.info(f'强制运行，检测到账户 {netflix_account_email} 存在密码被重置的邮件，已触发密码重置流程')

                                return netflix_account_email

                            logger.info(f'首次运行，故今次检测账户 {netflix_account_email}，发现的都是一些旧的密码被重置的邮件，不做处理')

                            return None

                        return netflix_account_email
                elif mail_type == 1:
                    if self.is_password_reset_request(resp['text']):
                        logger.info('Netflix 账户 {} 已收到请求重置密码的邮件，开始提取重置链接', netflix_account_email)

                        self.redis.set(key_last_id, id)

                        match = Netflix.RESET_URL_REGEX.search(resp['text'])
                        if not match:
                            raise Exception('已命中重置密码邮件，但是未能正确提取重置密码链接，请调查一下')

                        logger.info('已成功提取重置密码链接')
                        logger.info(f'本次重置链接为：{match.group(0)} ID：{id}')

                        return match.group(0)
                else:
                    raise Exception('mail_type 仅支持传入 0 或 1，0 表示密码重置结果邮件，1 表示请求重置密码邮件')

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

    def __do_reset(self, netflix_account_email: str, p: str):
        """
        执行重置密码流程
        :param netflix_account_email:
        :param p:
        :return:
        """
        start_time = time.time()

        self.__forgot_password(netflix_account_email)

        logger.info('等待接收重置密码链接')

        # 坐等奈飞发送的重置密码链接
        wait_start_time = time.time()
        while True:
            reset_link = self.__fetch_mail(netflix_account_email, 1)

            if reset_link:
                self.redis.set(f'{netflix_account_email}.need_to_do', 0)  # 忽略下一封密码重置邮件

                break

            if (time.time() - wait_start_time) > 60 * self.max_wait_reset_mail_time:
                raise Exception(f'等待超过 {self.max_wait_reset_mail_time} 分钟，依然没有收到奈飞的重置密码来信，故将重走恢复密码流程')

            time.sleep(2)

        # 重置密码
        self.__reset_password_via_mail(reset_link, p)

        logger.info(f'今次自动重置密码耗时{Netflix.time_diff(start_time, time.time())}')

    @staticmethod
    def now(format='%Y-%m-%d %H:%M:%S.%f'):
        """
        当前时间
        精确到毫秒
        :return:
        """
        return datetime.datetime.now().strftime(format)[:-3]

    def __screenshot(self, filename: str):
        """
        截图
        :param filename:
        :return:
        """
        dir = os.path.dirname(filename)
        if not os.path.exists(dir):
            os.makedirs(dir)

        self.driver.save_screenshot(filename)

        return True

    @staticmethod
    def symbol_replace(val):
        real_val = val.group()
        if real_val == '{':
            return '@<@'
        elif real_val == '}':
            return '@>@'
        else:
            return ''

    @staticmethod
    def send_mail(subject: str, content: str or tuple, to=None, template='default') -> None:
        """
        发送邮件
        :param subject:
        :param content:
        :param to:
        :param template:
        :return:
        """
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
        if isinstance(content, tuple):
            with open('./mail/{}.html'.format(template), 'r', encoding='utf-8') as f:
                template_content = f.read()
                text = Netflix.MAIL_SYMBOL_REGEX.sub(Netflix.symbol_replace, template_content).format(*content)
                real_content = text.replace('@<@', '{').replace('@>@', '}')
        elif isinstance(content, str):
            real_content = content
        else:
            raise TypeError(f'邮件内容类型仅支持 list 或 str，当前传入的类型为 {type(content)}')

        # 邮件内容有多个部分
        msg = MIMEMultipart('alternative')

        msg['From'] = formataddr(('Im Robot', username))
        msg['To'] = formataddr(('', to))
        msg['Subject'] = subject

        # 添加网页
        page = MIMEText(real_content, 'html', 'utf-8')
        msg.attach(page)

        # 添加 html 内联图片，仅适配模板中头像
        if isinstance(content, tuple):
            with open('mail/images/ting.jpg', 'rb') as img:
                avatar = MIMEImage(img.read())
                avatar.add_header('Content-ID', '<avatar>')
                msg.attach(avatar)

        with smtplib.SMTP_SSL(host=host, port=port) if secure == 'ssl' else smtplib.SMTP(host=host,
                                                                                         port=port) as server:
            # 启用 tls 加密，优于 ssl
            if secure == 'tls':
                server.starttls(context=ssl.create_default_context())

            server.login(username, password)
            server.sendmail(from_addr=username, to_addrs=to, msg=msg.as_string())

    @catch_exception
    def run(self):
        logger.info('开始监听密码被改邮件')

        # 监听密码被改邮件
        while True:
            real_today = Netflix.today_()
            if self.today != real_today:
                self.today = real_today
                self.__logger_setting()

            self.redis = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
            self.redis.set_response_callback('GET', int)

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                all_tasks = {executor.submit(self.__fetch_mail, item.get('u'), 0): item.get('p') for item in
                             self.MULTIPLE_NETFLIX_ACCOUNTS}

                for future in as_completed(all_tasks):
                    try:
                        p = all_tasks[future]
                        netflix_account_email = future.result()

                        if netflix_account_email:
                            for i in range(0, self.max_retry):
                                try:
                                    if i:
                                        logger.info(f'第 {i} 次重试恢复密码')

                                    self.__do_reset(netflix_account_email, p)

                                    Netflix.send_mail(f'发现有人修改了 Netflix 账户 {netflix_account_email} 的密码，我已自动将密码恢复初始状态',
                                                      f'在 {self.now()} 已将密码恢复为初始状态，本次自动处理成功。')

                                    break
                                except Exception as e:
                                    self.redis.set(f'{netflix_account_email}.need_to_do', 1)  # 恢复检测

                                    screenshot_file = f'logs/screenshots/{netflix_account_email}/{self.now("%Y-%m-%d_%H_%M_%S_%f")}.png'
                                    self.__screenshot(screenshot_file)
                                    logger.info(f'出错画面已被截图，图片文件保存在：{screenshot_file}')

                                    logger.error(f'密码恢复过程出错：{str(e)}，即将重试')
                            else:
                                logger.info(f'一共尝试 {self.max_retry} 次，均无法自动恢复密码，需要人工介入')

                                Netflix.send_mail('主人，多次尝试自动恢复密码均以失败告终，请您调查一下',
                                                  f'一共尝试了 {self.max_retry} 次，均无法自动恢复密码，需要人工介入。<br>每一次失败的原因我已写入日志，错误画面的截图也已经保存。<br><br>机器人敬上')
                    except Exception as e:
                        logger.error('出错：{}', str(e))

                time.sleep(2)

                logger.debug('开始下一轮监听')

    @staticmethod
    def send_keys_delay_random(element, keys, min_delay=0.13, max_delay=0.52):
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


if __name__ == '__main__':
    Netflix = Netflix()
    Netflix.run()
