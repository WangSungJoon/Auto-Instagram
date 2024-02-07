import csv
import json
import logging
import os
import random
import re
import sys
import time
import traceback
from datetime import date, datetime

import pandas as pd
import requests
import sqlalchemy
from dotenv import load_dotenv
from fake_useragent import UserAgent
from openai import OpenAI
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire import webdriver
from sqlalchemy import create_engine
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import SQLAlchemyError
from webdriver_manager.chrome import ChromeDriverManager

import config
import prompt

load_dotenv()


MAX_RETRY = 5


class AutoManager:
    def __init__(
        self, ID, account_grade, limit_follow, limit_like, limit_comment, limit_DM
    ):
        self.script_path = os.path.dirname(os.path.abspath(__file__))
        self.logs_folder = os.path.join(self.script_path, "logs")
        self.data_folder = os.path.join(self.script_path, "data")
        self.client = OpenAI(api_key=os.getenv("_OPENAI_API_KEY"))
        self.db_config = {
            "drivername": os.getenv("_DB_DRIVERNAME"),
            "host": os.getenv("_DB_HOST"),
            "port": int(os.getenv("_DB_PORT")),
            "username": os.getenv("_DB_USERNAME"),
            "password": os.getenv("_DB_PASSWORD"),
            "database": os.getenv("_DB_DATABASE"),
        }
        self.SQLENGHINE = create_engine(URL.create(**self.db_config))
        self.instagram_id = ID
        self.account_grade = account_grade.lower()
        self.instagram_pw = None
        self.chromedriver = None
        self.limit_follow = limit_follow
        self.limit_like = limit_like
        self.limit_comment = limit_comment
        self.limit_DM = limit_DM
        self.total_access = 0
        self.total_follow = 0
        self.total_request = 0
        self.total_like = 0
        self.total_comment = 0
        self.total_DM = 0
        self.error = []
        self.logger = self.init_logger()
        self.logger.info("* Init AutoManager")

    def init_logger(self, level=logging.INFO):
        account_folder_path = os.path.join(
            self.logs_folder, f"auto_manager/{self.instagram_id}"
        )
        if not os.path.exists(account_folder_path):
            os.makedirs(account_folder_path)

        # 로그 파일 경로 설정
        current_date = datetime.now().strftime("%Y%m%d")
        log_file_path = os.path.join(account_folder_path, f"{current_date}.log")

        # 개별 로거 생성 및 설정
        logger = logging.getLogger("AutoManagerLogger")
        logger.setLevel(level)

        # 로그 포맷 정의
        formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

        # 파일 핸들러 설정
        file_handler = logging.FileHandler(log_file_path)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # 스트림 핸들러 설정 (콘솔 출력)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        # 다른 핸들러에서 로그를 처리하게 하여, 로그 메시지가 중복으로 기록되지 않도록 설정
        logger.propagate = False
        return logger

    def execute_query(self, query, data=None):
        retry_count = 0
        while True:
            try:
                # 쿼리 실행
                with self.SQLENGHINE.begin() as conn:
                    if data:
                        execute = conn.execute(sqlalchemy.text(query), data)
                    else:
                        execute = conn.execute(sqlalchemy.text(query))

                    if query.strip().lower().startswith("select") or (
                        query.strip().lower().startswith("insert")
                        and "returning" in query.lower()
                    ):
                        return execute.fetchall()
                    else:
                        # 다른 유형의 쿼리인 경우 (예: INSERT, UPDATE, DELETE)
                        return None  # 결과 없음

            except SQLAlchemyError:
                if retry_count >= MAX_RETRY:
                    raise Exception("Maximum retry attempts reached.")
                time.sleep(retry_count)  # 다음 응답까지 retry_count초 대기
                retry_count += 1
                print(f"Timed out, retrying ({retry_count}/{MAX_RETRY})...")

    def check_account(self):
        # insta_account 테이블에서 user_id 조회 쿼리
        insert_query = """
            SELECT user_pw FROM insta_account
            WHERE user_id = :user_id
        """
        insert_data = {"user_id": self.instagram_id}

        # 쿼리 실행
        result = self.execute_query(insert_query, insert_data)

        # 조회 결과에 따라 처리
        if result:
            self.instagram_pw = result[0][0]
            if "dev" in self.instagram_id or "aikorea" in self.instagram_id:
                self.comment_prompt = prompt._COMMENT_PROMPT_dev_meme121
            if "purin" in self.instagram_id:
                self.comment_prompt = prompt._COMMENT_PROMPT_purinpurin_store
            return True
        else:
            return False

    def openai_create_nonstream(
        self,
        messages: str,
        model: str = "gpt-3.5-turbo-1106",
        temperature: float = 0,
        max_tokens: float = 2000,
    ) -> str:
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        answer = response.choices[0].message.content
        return answer

    def account_evaluator(self):
        target_account = self.chromedriver.current_url.split("/")[3]

        # 계정 유효성 확인
        try:
            # 팔로우 버튼 수집
            btn_follow = WebDriverWait(self.chromedriver, 10).until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        """div._ap3a._aaco._aacw._aad6._aade""",
                    ),
                ),
            )
        except:
            # 죄송합니다. 페이지를 사용할 수 없습니다.
            self.logger.info(f"""Can't Access to {target_account} Account.""")
            return False

        # 기존 수행 여부 확인
        if btn_follow.text == "팔로잉":
            self.logger.info(f"""Account {target_account} Already Followed.""")
            return False
        if btn_follow.text == "요청됨":
            self.logger.info(f"""{target_account} Is Under Request.""")
            return False

        # 게시물 박스로 게시글 / 비공계 여부 확인
        article_wrapper = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """section > main > div""",
                ),
            ),
        )
        try:
            # 게시물 유무 확인
            article_wrapper.find_element(By.CSS_SELECTOR, """div > article""")
        except:
            try:
                # 비공개 계정 Pass
                article_wrapper.find_element(By.CSS_SELECTOR, """h2._aa_u""")
                self.logger.info(f"""{target_account} is Private Account.""")
                return False
            except:
                # 게시물 없는 계정 Pass
                self.logger.info("""No Article Account.""")
                return False

        # 팔로워 수 수집
        num_follower = self.chromedriver.find_element(
            By.CSS_SELECTOR,
            """li:nth-child(2) > a > span > span""",
        ).text

        # 계정 설명 글 수집
        try:
            num_description = len(
                self.chromedriver.find_element(
                    By.CSS_SELECTOR,
                    """h1._ap3a._aaco._aacu._aacx._aad6._aade""",
                ).text
            )
        except:
            num_description = 0

        # 게시글 수 수집
        num_articles = len(
            self.chromedriver.find_elements(
                By.CSS_SELECTOR,
                """div > article > div:nth-child(1) > div > div > div > a""",
            )
        )
        self.logger.info(
            f"num_follower : {num_follower} / num_description : {num_description} / num_articles : {num_articles}"
        )

        # 등급 판별
        grade_result = "common"
        if re.search(r"[^\d.]", num_follower):
            grade = "influencer"
        else:
            for grade, criteria in config.grade_table.items():
                if (
                    float(num_follower) >= criteria["follower"]
                    and num_description >= criteria["description"]
                    and num_articles >= 10
                ):
                    grade_result = grade
                    break
        self.logger.info(f"{grade_result[0].upper() + grade_result[1:]} Grade.")

        # 팔로워 수 semipro 이상일 경우 계정 기록
        if config.score_table[grade_result] >= config.score_table["semipro"]:
            self.add_account_list(target_account, self.chromedriver.current_url)

        if config.score_table[grade_result] >= config.score_table[self.account_grade]:
            return grade_result
        else:
            return False

    def check_constrict(self):
        try:
            # 나중에 다시 시도하세요, 조치를 취하지못하도록 1주일 차단
            dialog_window = WebDriverWait(self.chromedriver, 5).until(
                EC.presence_of_all_elements_located(
                    (
                        By.CSS_SELECTOR,
                        """div.x7r02ix.xf1ldfh.x131esax.xdajt7p.xxfnqb6.xb88tzc.xw2csxc.x1odjw0f.x5fp0pe""",
                    ),
                ),
            )[-1]

            # 확인 버튼
            btn_chk = dialog_window.find_elements(By.CSS_SELECTOR, """button""")[-1]
            self.chromedriver.execute_script("arguments[0].click();", btn_chk)
            raise AccountBlocked
        except TimeoutException:
            try:
                # 현재 계정을 팔로우 할 수 없습니다.
                WebDriverWait(self.chromedriver, 1).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, """div._a9-v"""))
                )
                raise FollowingConstricted
            except TimeoutException:
                try:
                    # 몇일 동안 활동이 정지되었습니다
                    WebDriverWait(self.chromedriver, 1).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, """div.x78zum5 xdt5ytf xl56j7k-v""")
                        ),
                    )
                    self.logger.warning("Currently Account Is Blocked.")
                    raise AccountBlocked
                except TimeoutException:
                    pass

    def follow(self, flag):
        target_account = self.chromedriver.current_url.split("/")[3]

        # 팔로우 버튼 탐색
        btn_follow = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div._ap3a._aaco._aacw._aad6._aade""",
                ),
            ),
        )
        if flag:
            if btn_follow.text == "팔로우" or btn_follow.text == "맞팔로우 하기":
                self.chromedriver.execute_script("arguments[0].click();", btn_follow)

                self.check_constrict()

                # 게시물 박스 수집
                article_wrapper = WebDriverWait(self.chromedriver, 10).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            """section > main > div""",
                        ),
                    ),
                )
                try:
                    # 비공개 여부 확인
                    article_wrapper.find_element(By.CSS_SELECTOR, """h2._aa_u""")
                    self.total_request += 1
                    self.logger.info(f"""Requests Follow {target_account}""")
                except:
                    self.total_follow += 1
                    self.logger.info(f"""Follow {target_account}""")
        else:
            if btn_follow.text == "팔로잉":
                self.chromedriver.execute_script("arguments[0].click();", btn_follow)

                # 팔로우 취소 버튼 탐색
                btn_cancel_follow = WebDriverWait(self.chromedriver, 10).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            """div.x7r02ix.xf1ldfh.x131esax.xdajt7p.xxfnqb6.xb88tzc.xw2csxc.x1odjw0f.x5fp0pe > div > div > div > div:nth-child(8)""",
                        ),
                    ),
                )
                self.chromedriver.execute_script(
                    "arguments[0].click();", btn_cancel_follow
                )
                self.logger.info(f"""Unfollowed {target_account}.""")
            else:
                self.logger.info(f"""Not Following {target_account} yet.""")
        time.sleep(1)

    def DM_sender(self):
        target_account = self.chromedriver.current_url.split("/")[3]

        # 메세지 보내기 클릭
        btn_send_DM = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div.x9f619.xjbqb8w.x78zum5.x168nmei.x13lgxp2.x5pf9jr.xo71vjh.x1i64zmx.x1n2onr6.x6ikm8r.x10wlt62 > div""",
                ),
            ),
        )
        self.chromedriver.execute_script("arguments[0].click();", btn_send_DM)
        time.sleep(5)  # 일반 화면전환은 5초

        try:
            # 알람 설정 창
            alarm_window = WebDriverWait(self.chromedriver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, """div._a9-v"""))
            )
            btn_later = alarm_window.find_element(
                By.CSS_SELECTOR, """button._a9--._ap36._a9_1"""
            )
            self.chromedriver.execute_script("arguments[0].click();", btn_later)
        except TimeoutException:
            pass

        # DM message 로드
        if "dev" in self.instagram_id:
            message = config._DM_TEMPLATE_dev_meme121
        if "aikorea" in self.instagram_id:
            message = config._DM_TEMPLATE_aikorea121
        if "purin" in self.instagram_id:
            message = config._DM_TEMPLATE_purinpurin_store

        # 입력창 탐색
        textbox = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div[role="textbox"]""",
                ),
            ),
        )
        actions = ActionChains(self.chromedriver)
        actions.move_to_element(textbox).click().send_keys(message).send_keys(
            Keys.RETURN
        ).perform()

        self.total_DM += 1
        self.logger.info(f"Send DM to {target_account}")
        time.sleep(5)  # 일반 화면전환은 5초

        self.chromedriver.back()

    def add_account_list(self, target_account, account_url):
        add_account_list_path = os.path.join(
            self.data_folder, f"csv/{self.instagram_id}/add_accounts.csv"
        )

        row_data = {"account": target_account, "account_url": account_url}

        if not os.path.exists(add_account_list_path):
            with open(
                add_account_list_path, mode="w", newline="", encoding="utf-8"
            ) as file:
                csv_writer = csv.writer(file)
                csv_writer.writerow(row_data.keys())
                csv_writer.writerow(row_data.values())
        else:
            # 파일이 존재하면 데이터프레임으로 읽어서 확인 후 추가
            with open(
                add_account_list_path, mode="r", newline="", encoding="utf-8"
            ) as file:
                csv_reader = csv.reader(file)
                accounts = [row[0] for row in csv_reader]

            # target_account가 accounts에 없으면 파일에 추가
            if target_account not in accounts:
                with open(
                    add_account_list_path, mode="a", newline="", encoding="utf-8"
                ) as file:
                    csv_writer = csv.writer(file)
                    csv_writer.writerow(row_data.values())
            else:
                return
        self.logger.info(
            f"{target_account} Is Added At add_accounts_{self.instagram_id}.csv"
        )

    def like(self):
        # 좋아요 버튼 탐색
        try:
            btn_like = WebDriverWait(self.chromedriver, 10).until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        """span._aamw > div""",
                    ),
                ),
            )
        except:
            raise Exception("Like Not Opened")

        status = btn_like.find_element(
            By.CSS_SELECTOR,
            "svg",
        )
        if status.get_attribute("aria-label") == "좋아요":
            self.chromedriver.execute_script("arguments[0].click();", btn_like)

            try:
                # 나중에 다시 시도사세요, 조치를 취하지못하도록 1주일 차단
                dialog_window = WebDriverWait(self.chromedriver, 3).until(
                    EC.presence_of_all_elements_located(
                        (
                            By.CSS_SELECTOR,
                            """div.x7r02ix.xf1ldfh.x131esax.xdajt7p.xxfnqb6.xb88tzc.xw2csxc.x1odjw0f.x5fp0pe""",
                        ),
                    ),
                )[-1]

                # 확인 버튼
                btn_chk = dialog_window.find_elements(By.CSS_SELECTOR, """button""")[-1]
                self.chromedriver.execute_script("arguments[0].click();", btn_chk)
                raise AccountBlocked
            except TimeoutException:
                pass

            self.total_like += 1
            # time.sleep(random.randint(1, 3))
            return True
        else:
            # self.logger.info("Already Liked")
            return False

    def comment_poster(self):
        # 기존 댓글 여부 확인
        comment_account_list = self.chromedriver.find_elements(
            By.CSS_SELECTOR,
            """div > ul > div:nth-child(3) > div > div > div > ul > div > li > div > div > div._a9zr > h3 > div > span > div > a""",
        )
        for comment_account in comment_account_list:
            if comment_account.text == self.instagram_id:
                self.logger.info("Already commented post.")
                raise AlreadyExecuted

        if "dev" in self.instagram_id or "aikorea" in self.instagram_id:
            # 댓글 생성
            messages = [
                {
                    "role": "system",
                    "content": self.comment_prompt,
                }
            ]
            # gpt-3.5-turbo-1106
            # gpt-4-1106-preview
            comment = self.openai_create_nonstream(
                messages, model="gpt-4-1106-preview", temperature=0.5
            )
        if "purin" in self.instagram_id:
            comment = random.choice(config._COMMENT_LIST_purinpurin_store)
        self.logger.info(f"""Comment : {comment}""")

        # 입력창 탐색
        try:
            commentarea = self.chromedriver.find_element(
                By.CSS_SELECTOR, """textarea"""
            )
        except:
            return self.logger.info("No Textarea In This Article.")

        actions = ActionChains(self.chromedriver)
        actions.move_to_element(commentarea).click().send_keys(comment).send_keys(
            Keys.RETURN
        ).perform()
        self.logger.info("Insert comment.")

        try:
            # 나중에 다시 시도사세요, 조치를 취하지못하도록 1주일 차단
            dialog_window = WebDriverWait(self.chromedriver, 5).until(
                EC.presence_of_all_elements_located(
                    (
                        By.CSS_SELECTOR,
                        """div.x7r02ix.xf1ldfh.x131esax.xdajt7p.xxfnqb6.xb88tzc.xw2csxc.x1odjw0f.x5fp0pe""",
                    ),
                ),
            )[-1]

            # 확인 버튼
            btn_chk = dialog_window.find_elements(By.CSS_SELECTOR, """button""")[-1]
            self.chromedriver.execute_script("arguments[0].click();", btn_chk)
            raise AccountBlocked
        except TimeoutException:
            pass

        self.logger.info("Posting Comment Complete.")
        self.total_comment += 1

        return True

    def close_article(self):
        btn_close = self.chromedriver.find_element(
            By.CSS_SELECTOR, """div.x160vmok.x10l6tqk.x1eu8d0j.x1vjfegm > div > div"""
        )
        self.chromedriver.execute_script("arguments[0].click();", btn_close)
        time.sleep(random.uniform(1, 5))

    def error_recoder(self):
        screenshot_path = os.path.join(
            self.logs_folder, f"""err/{datetime.now().strftime("%Y%m%d%I%m")}.png"""
        )
        self.chromedriver.save_screenshot(screenshot_path)

    def init_chromedriver(self):
        options = webdriver.ChromeOptions()
        # options.add_argument("--headless")
        # options.add_argument("--no-sandbox")
        # fake_useragent 라이브러리를 사용하여 무작위 사용자 에이전트를 생성
        options.add_argument("user-agent=%s" % UserAgent().random)
        options.add_argument("--ignore-certificate-errors")
        # GPU 사용을 방지하여 픽셀 및 GPU 가속 비활성화
        # options.add_argument("--disable-gpu")
        # options.add_argument("--disable-software-rasterizer")
        # 자동화된 소프트웨어에서 사용되는 일부 기능들을 비활성화
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Selenium이 자동화된 브라우저임을 나타내는 'enable-automation' 플래그를 비활성화합니다.
        # 브라우저가 자동화된 것처럼 보이는 몇몇 특성들을 제거
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        # 자동화 확장 기능을 비활성화합니다. 이것도 자동화 탐지를 우회하는 데 도움을 줄 수 있습니다.
        options.add_experimental_option("useAutomationExtension", False)
        # 웹 페이지에서 이미지 로딩을 차단합니다. 페이지 로딩 속도를 빠르게 하고, 데이터 사용량을 줄이는 데 유용합니다.
        # '2'는 이미지 로드를 차단하는 것을 의미합니다.
        # options.add_experimental_option(
        #     "prefs",
        #     {"profile.managed_default_content_settings.images": 2},
        # )

        # 모바일 세팅
        # user_agt = 'Mozilla/5.0 (Linux; Android 9; INE-LX1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.45 Mobile Safari/537.36'
        # options.add_argument(f'user-agent={user_agt}')
        # options.add_argument("window-size=412,950")
        # options.add_experimental_option("mobileEmulation", {
        #     "deviceMetrics": {
        #             "width": 360,
        #             "height": 760,
        #             "pixelRatio": 3.0
        #         }
        # })

        # Chrome WebDriver 생성
        self.chromedriver = webdriver.Chrome(
            service=ChromeService(ChromeDriverManager().install()),
            options=options,
        )
        # 크롤링 방지 설정을 undefined로 변경
        self.chromedriver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                    """,
            },
        )

    def login(self):
        try:
            # 로그인 프로세스
            self.chromedriver.get("https://www.instagram.com/accounts/login/")

            id_input = WebDriverWait(self.chromedriver, 10).until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        """#loginForm > div > div:nth-child(1) > div > label > input""",
                    )
                ),
            )
            id_input.send_keys(self.instagram_id)

            pw_input = self.chromedriver.find_element(
                By.CSS_SELECTOR,
                """#loginForm > div > div:nth-child(2) > div > label > input""",
            )
            pw_input.send_keys(self.instagram_pw)

            btn_login = self.chromedriver.find_element(
                By.CSS_SELECTOR, """#loginForm > div > div:nth-child(3) > button"""
            )
            self.chromedriver.execute_script("arguments[0].click();", btn_login)
            self.logger.info(f"{self.instagram_id} Login Success.")
            time.sleep(10)  # 로드 필요시 10초
        except:
            raise ("Login Fail Error")

    def get_follow_list(self):
        follow_list_path = os.path.join(
            self.data_folder, f"csv/{self.instagram_id}/follow_list.csv"
        )
        df = pd.read_csv(follow_list_path, header=0, encoding="utf-8")
        # DataFrame이 비어있는지 확인
        if df.empty:
            raise EmptyFollowList

        follow_list = df.iloc[:, 3].tolist()  # 네 번째 열의 데이터를 리스트로 추출
        self.logger.info("Road Follow List Complete.")
        return follow_list

    def record_account_status(self):
        account_path = os.path.join(self.logs_folder, f"screenshot/{self.instagram_id}")

        if not os.path.exists(account_path):
            os.makedirs(account_path)

        # 오늘 저장 여부 확인
        current_date = datetime.now().strftime("%Y%m%d")
        screenshot_path = os.path.join(account_path, f"{current_date}.png")
        if not os.path.exists(screenshot_path):
            # 프로필로 이동
            btn_profile = WebDriverWait(self.chromedriver, 10).until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        """div.x1iyjqo2.xh8yej3 > div:nth-child(8) > div > span > div > a""",
                    ),
                ),
            )
            self.chromedriver.execute_script("arguments[0].click();", btn_profile)
            time.sleep(10)  # 로드 필요시 10초

            # 게시물 / 팔로워 / 팔로우 수 수집
            status_wrapper = WebDriverWait(self.chromedriver, 10).until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        """section > ul""",
                    ),
                ),
            )
            num_article = status_wrapper.find_element(
                By.CSS_SELECTOR,
                """li:nth-child(1) > span > span""",
            ).text
            num_follower = status_wrapper.find_element(
                By.CSS_SELECTOR,
                """li:nth-child(2) > a > span > span""",
            ).text
            num_follow = status_wrapper.find_element(
                By.CSS_SELECTOR,
                """li:nth-child(3) > a > span > span""",
            ).text

            # 현황 DB에 저장
            insert_query = """
                INSERT INTO insta_status_log (user_id, article, follower, follow)
                VALUES (:user_id, :article, :follower, :follow)
            """
            # 데이터 삽입을 위한 딕셔너리
            insert_data = {
                "user_id": self.instagram_id,
                "article": num_article,
                "follower": num_follower,
                "follow": num_follow,
            }
            self.execute_query(insert_query, insert_data)
            self.logger.info(
                f"{current_date} : Article : {num_article} / Follower : {num_follower} / Follow : {num_follow}"
            )
            self.logger.info("Completely Record Status On The Server.")

            # 스크린샷 캡처 저장
            self.chromedriver.save_screenshot(screenshot_path)
            self.logger.info("Saving Screenshot Complete.")

    def check_routine_status(self):
        # PostgreSQL 쿼리 실행
        insert_query = f"""
            SELECT follow, likes, comment, DM
            FROM auto_manager_log
            WHERE user_id = '{self.instagram_id}'
            AND DATE(datetime) = '{date.today()}';
        """
        logs = self.execute_query(insert_query)

        today_follows = sum(log[0] for log in logs)
        today_likes = sum(log[1] for log in logs)
        today_comments = sum(log[2] for log in logs)
        today_DM = sum(log[3] for log in logs)

        if (
            today_follows > 80
            or today_likes > 900
            or today_comments > 150
            or today_DM > 80
        ):
            return False
        else:
            return True

    def remove_profile_from_csv(self, target_profile):
        follow_list_path = os.path.join(
            self.data_folder, f"csv/{self.instagram_id}/follow_list.csv"
        )
        with open(follow_list_path, "r") as file:
            lines = file.readlines()

        # 첫 번째 계정 제거
        lines.pop(1)

        with open(follow_list_path, "w") as file:
            file.writelines(lines)
        self.logger.info(f"Remove {target_profile} From profile_list")

    def send_kakao_message(self, text):
        _KAKAO_TOKEN = "BxuBS1BzIR8Ax9UJOqnAqxTYRoPavRuMsYoKPXQRAAABjLDcAn5SGUcvaFb1Eg"

        url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {_KAKAO_TOKEN}",
        }

        # 전송할 JSON 데이터
        template_object = {
            "object_type": "text",
            "text": text,
            "link": {
                "web_url": "https://developers.kakao.com",
                "mobile_web_url": "https://developers.kakao.com",
            },
            "button_title": "바로 확인",
        }

        data = {"template_object": json.dumps(template_object)}

        # 데이터 전송
        response = requests.post(url, headers=headers, data=data)

        if response.status_code == 200:
            self.logger.info("Sending Kakao Message Complete")
        else:
            print(response)

    def send_report(self, text):
        if "aikorea" in self.instagram_id or "dev_meme" in self.instagram_id:
            self.chromedriver.get("https://www.instagram.com/popowsj")
        if "purin" in self.instagram_id:
            self.chromedriver.get("https://www.instagram.com/cocoroso_")
        time.sleep(10)  # 로드 필요시 10초

        # 메세지 보내기 클릭
        btn_send_DM = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div.x9f619.xjbqb8w.x78zum5.x168nmei.x13lgxp2.x5pf9jr.xo71vjh.x1i64zmx.x1n2onr6.x6ikm8r.x10wlt62 > div""",
                ),
            ),
        )
        self.chromedriver.execute_script("arguments[0].click();", btn_send_DM)
        time.sleep(10)  # 로드 필요시 10초

        try:
            # 알람 설정 창
            alarm_window = WebDriverWait(self.chromedriver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, """div._a9-v"""))
            )
            btn_later = alarm_window.find_element(
                By.CSS_SELECTOR, """button._a9--._ap36._a9_1"""
            )
            self.chromedriver.execute_script("arguments[0].click();", btn_later)
        except TimeoutException:
            pass

        # 입력창 탐색
        textbox = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div[role="textbox"]""",
                ),
            ),
        )
        actions = ActionChains(self.chromedriver)
        actions.move_to_element(textbox).click().send_keys(text).send_keys(
            Keys.RETURN
        ).perform()
        time.sleep(5)  # 일반 화면전환은 5초

        self.logger.info("Completely Send Report.")

    def routine_worker(self, profile_url):
        try:
            cnt_like = 0
            target_account = profile_url.split("/")[3]
            self.logger.info(
                f"""* Start : {target_account} Account Start Routine Work."""
            )

            # 프로필 페이지 이동
            self.chromedriver.get(profile_url)
            self.total_access += 1
            time.sleep(10)

            # 수행 가능 여부 판단
            grade = self.account_evaluator()
            if not grade:
                return

            # semiinfluencer 이상은 좋아요 댓글만
            # semiinfluencer 이하는 팔,DM,댓글,좋아요
            # 팔로우
            if config.score_table[grade] < config.score_table["semiinfluencer"]:
                if self.limit_follow != -1:
                    self.follow(True)

                # DM
                if self.limit_DM != -1:
                    self.DM_sender()

            # 스크롤 이동
            self.chromedriver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight);",
            )
            time.sleep(5)
            articles = self.chromedriver.find_elements(
                By.CSS_SELECTOR,
                """div > article > div:nth-child(1) > div > div > div > a""",
            )
            # self.logger.info("Get article_list.")

            # 전체 게시물 중 랜덤한 개수의 랜덤한 게시물에 좋아요
            random_idx = [0] + random.sample(
                range(1, len(articles)), random.randint(2, 5)
            )
            random_idx.sort()
            for idx in random_idx:
                try:
                    article = articles[idx]
                    # 게시글 클릭
                    self.chromedriver.execute_script("arguments[0].click();", article)
                    time.sleep(3)  # 일반 화면전환은 5초

                    # 좋아요
                    if self.limit_like != -1:
                        if self.like():
                            cnt_like += 1

                    # 첫번째 게시물에 댓글
                    if (
                        self.limit_comment != -1
                        and config.score_table[grade] < config.score_table["influencer"]
                        and idx == 0
                    ):
                        self.comment_poster()

                    # 게시글 닫기
                    try:
                        self.close_article()
                    except Exception:
                        self.logger.warning("Click btn_close Is Intercepted.")
                        self.chromedriver.get(profile_url)
                        time.sleep(10)  # 로드 필요시 10초
                except AlreadyExecuted:
                    break
                except AccountBlocked as e:
                    raise (e)
                except Exception as e:
                    if str(e) == "Like Not Opened":
                        self.logger.warning("Need To Follow To Like Articles.")
                    else:
                        self.logger.warning("알수없는 오류가 발생하였습니다.")
                        self.logger.error(f"{traceback.format_exc()}")
                        self.error_recoder()
                    break

            # 계정 좋아요 개수
            self.logger.info(f"{cnt_like} Like Clicked.")
        except AlreadyExecuted:
            pass
        finally:
            # 수행된 프로필 파일목록에서 제거
            self.remove_profile_from_csv(target_account)
            self.logger.info(f"""* End : {target_account} Account Routine Work End.""")

    def run(self):
        self.logger.info("Operating Auto Follow Managing.")
        start_time = time.time()

        # Main Process
        try:
            # 프로필 리스트 수집
            follow_list = self.get_follow_list()

            # self.chromedriver 생성
            self.init_chromedriver()

            # 로그인
            self.login()

            # 계정 현황 기록
            self.record_account_status()

            # Daily 제한 여부 확인
            if not self.check_routine_status():
                return self.logger.warning("Reach To Daily Limit.")

            for profile_url in follow_list:
                # 실행 조건 확인
                if (
                    (self.limit_follow == -1 and self.limit_comment == -1)
                    or (
                        self.limit_follow == -1
                        and self.total_comment == self.limit_comment
                    )
                    or (
                        self.limit_follow != -1
                        and self.total_follow == self.limit_follow
                    )
                ):
                    break

                # 루틴 작업 수행
                self.routine_worker(profile_url)

                self.logger.info(
                    f"""* Current : Access : {self.total_access} / Follow {self.total_follow} / Request {self.total_request} / {self.total_like} Likes / {self.total_comment} Comments / {self.total_DM} DM Done."""
                )
                self.logger.info(
                    """---------------------------------------------------"""
                )
        except EmptyFollowList:
            self.logger.warning("Follow List Is Empty")
            self.error.append("EmptyFollowList")
        except FollowingConstricted:
            self.logger.warning("Currently Following Is Constricted.")
            self.error.append("FollowingConstricted")
        except AccountBlocked:
            self.logger.warning("Currently Account Is Constricted")
            self.error.append("AccountBlocked")
        except Exception:
            self.logger.error("Unknown Error Occurred")
            self.logger.error(f"{traceback.format_exc()}")
            self.error_recoder()
        finally:
            runtime = round(time.time() - start_time)
            runtime_kor = (
                f"""{f"{runtime//60}분 " if runtime//60>0  else ""}{runtime%60}초."""
            )
            runtime_eng = f"""{f"{runtime//60} min " if runtime//60>0  else ""}{runtime%60} sec."""
            currenttime = (
                f"""{datetime.now().strftime("%Y년 %m월 %d일 %p %I시 %M분")}"""
            )

            self.logger.info("""-------------- Complete Auto Managing Process.""")
            self.logger.info(f"""-------------- {runtime_eng}""")
            self.logger.info(f"""-------------- {currenttime}""")
            self.logger.info(f"""-------------- {self.instagram_id}""")
            self.logger.info(f"""-------------- total_access : {self.total_access}""")
            self.logger.info(f"""-------------- total_follow : {self.total_follow}""")
            self.logger.info(f"""-------------- total_like : {self.total_like}""")
            self.logger.info(f"""-------------- total_comment : {self.total_comment}""")
            self.logger.info(f"""-------------- total_DM : {self.total_DM}""")

            # 결과 DB에 저장
            insert_query = """
                INSERT INTO auto_manager_log (user_id, access, follow, likes, comment, DM)
                VALUES (:user_id, :access, :follow, :likes, :comment, :DM)
            """
            # 데이터 삽입을 위한 딕셔너리
            insert_data = {
                "user_id": self.instagram_id,
                "access": self.total_access,
                "follow": self.total_follow,
                "likes": self.total_like,
                "comment": self.total_comment,
                "DM": self.total_DM,
            }
            self.execute_query(insert_query, insert_data)
            self.logger.info("Completely Record Result On The Server.")
            message = "자동 인스타 활동 수행 완료!                 "
            message += f"""{currenttime}         """
            message += f"""걸린시간 : {runtime_kor}                                  """
            message += f"""ID : {self.instagram_id}                                          """
            message += f"""총 접근 : {self.total_access}                                          """
            message += f"""총 팔로우 : {self.total_follow} / {self.limit_follow}                                          """
            message += f"""총 좋아요 : {self.total_like}                                          """
            message += f"""총 댓글 : {self.total_comment}                                          """
            message += f"""총 DM : {self.total_DM}"""
            if len(self.error) > 0:
                message += f"""                                          error : {", ".join(self.error)}"""

            # self.send_kakao_message(message)
            self.send_report(message)

            if self.chromedriver:
                self.chromedriver.quit()
                self.logger.info("Quit Chromedriver.")


class EmptyFollowList(Exception):
    pass


class AlreadyExecuted(Exception):
    pass


class FollowingConstricted(Exception):
    pass


class AccountBlocked(Exception):
    pass


if __name__ == "__main__":
    ID = sys.argv[1]
    account_grade = sys.argv[2]
    limit_follow = int(sys.argv[3])
    limit_like = int(sys.argv[4])
    limit_comment = int(sys.argv[5])
    limit_DM = int(sys.argv[6])
    delay = int(sys.argv[7])

    # 50%의 확률로 실행될 코드
    # if random.choice([True, False]):
    if random.choice([True]):
        # 매시간 50%확률로 실행
        # 10~30분 중 랜덤한 시간 지연 후 실행
        # 입력한 팔로우 제한 수 내 랜덤한 수 만큼만 팔로우
        # semiinfluencer 이상은 좋아요 댓글만
        # semiinfluencer 이하는 팔,DM,댓글,좋아요
        # 첫번째 게시물에 댓글
        # 전체 게시물 중 랜덤한 개수의 랜덤한 게시물에 좋아요
        # 모든 동작 후 1~5초 중 랜덤한 시간만큼 대기

        # 10~30분 중 랜덤한 시간 지연 후 실행
        delay_time = 0
        delay_time = random.randint(10, 30) * 60
        if delay:
            time.sleep(delay_time)

        # AutoManager 클래스의 인스턴스를 생성합니다.
        auto_manager = AutoManager(
            ID,
            account_grade,
            # 입력한 팔로우 제한 수 내 랜덤한 수 만큼만 팔로우
            -1 if limit_follow == -1 else random.randint(1, limit_follow),
            limit_like,
            limit_comment,
            limit_DM,
        )
        if delay:
            auto_manager.logger.info(f"Started {delay_time} Min Later.")

        # 계정 유무 확인
        if auto_manager.check_account():
            # 자동 관리 프로세스를 실행합니다.
            auto_manager.run()
            # if operation == "follow":
            # elif operation == "unfollow":
            #     auto_manager.unfollow_worker()
        else:
            auto_manager.logger.warning("Not Exist Account")
