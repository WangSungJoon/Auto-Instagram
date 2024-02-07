import json
import logging
import os
import random
import sys
import time
import traceback
import urllib
from datetime import datetime

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

import config
import prompt

load_dotenv()

MAX_RETRY = 5


class AutoPosting:
    def __init__(self, ID):
        self.script_path = os.path.dirname(os.path.abspath(__file__))
        self.logs_folder = os.path.join(self.script_path, "logs")
        self.data_folder = os.path.join(self.script_path, "data")
        self.client = OpenAI(api_key=os.getenv("_OPENAI_API_KEY"))
        self.papago_id = os.environ.get("_PAPAGOID")
        self.papago_pw = os.environ.get("_PAPAGOPW")
        self.db_config = {
            "drivername": os.environ.get("_DB_DRIVERNAME"),
            "host": os.environ.get("_DB_HOST"),
            "port": int(os.environ.get("_DB_PORT")),
            "username": os.environ.get("_DB_USERNAME"),
            "password": os.environ.get("_DB_PASSWORD"),
            "database": os.environ.get("_DB_DATABASE"),
        }
        self.SQLENGHINE = create_engine(URL.create(**self.db_config))
        self.instagram_id = ID
        self.instagram_pw = None
        self.chromedriver = None
        if "dev" in self.instagram_id:
            self.topic_keywords = config._DEV_TOPIC_KEYWORDS
            self.generate_episode_prompt = prompt._GENERATE_DEV_EPISODE_PROMPT
            self.posting_text = config._DEV_POSTING_TEXT
        if "kor" in self.instagram_id:
            self.topic_keywords = config._KOR_TOPIC_KEYWORDS
            self.generate_kor_image_prompt = prompt._GENERATE_KOR_IMAGE_PROMPT
            self.posting_text = config._KOR_POSTING_TEXT
        self.logger = self.init_logger()
        self.logger.info("* Init AutoPosting")

    def init_logger(self, level=logging.INFO):
        account_folder = os.path.join(
            self.logs_folder, f"auto_posting/{self.instagram_id}"
        )
        if not os.path.exists(account_folder):
            os.makedirs(account_folder)

        # 개별 로거 생성 및 설정
        logger = logging.getLogger("AutoPostingLogger")
        logger.setLevel(level)

        # 로그 파일 경로 설정
        current_date = datetime.now().strftime("%Y%m%d")
        log_file_path = os.path.join(account_folder, f"{current_date}.log")

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
            # 계정이 존재하지 않는 경우
            return False

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
            service=ChromeService(),
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
            time.sleep(10)  # 로드 필요시 10초
            self.logger.info(f"{self.instagram_id} Login Success.")
        except:
            raise ("Login Fail Error")

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

    def openai_create_image(
        self,
        prompt: str,
        size="1024x1024",
        quality="hd",
    ) -> str:
        # OpenAI 라이브러리를 사용하여 이미지 생성 요청 보내기
        response = self.client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
            quality=quality,
            n=1,
        )

        return response.data[0].url

    def papago_translate(self, input: str, origin: str, trans: str) -> str:
        encText = urllib.parse.quote(input)
        data = f"source={origin}&target={trans}&text=" + encText
        url = "https://openapi.naver.com/v1/papago/n2mt"
        request = urllib.request.Request(url)
        request.add_header("X-Naver-Client-Id", self.papago_id)
        request.add_header("X-Naver-Client-Secret", self.papago_pw)
        response = urllib.request.urlopen(request, data=data.encode("utf-8"))
        rescode = response.getcode()

        if rescode == 200:
            response_body = response.read()
            result = response_body.decode("utf-8")
            result_json = json.loads(result)
            return result_json["message"]["result"]["translatedText"]
        else:
            self.logger.info("Error Code:" + rescode)
            return None

    def count_days(self):
        input_date_str = "2023-12-19"
        input_date = datetime.strptime(input_date_str, "%Y-%m-%d")
        current_date = datetime.now()
        difference = current_date - input_date
        return difference.days

    def send_kakao_message(self, text):
        _KAKAO_TOKEN = "eNppRu0m15OQxLJAr4M-05fICcY5KpP6oecKKiUNAAABjLQUD3FSGUcvaFb1Eg"

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

        # JSON 데이터를 URL 인코딩하여 문자열로 변환
        template_object_encoded = {"template_object": json.dumps(template_object)}

        # 데이터 전송
        response = requests.post(url, headers=headers, data=template_object_encoded)

        if response.status_code == 200:
            self.logger.info("Sending Kakao Message Complete.")

    def generate_prompt(self, src_path):
        # 주제 선정
        random_topic_kor = random.choice(list(self.topic_keywords.keys()))
        random_topic_eng = self.topic_keywords[random_topic_kor]
        self.logger.info(f"Topic : {random_topic_kor}")

        if "dev" in self.instagram_id:
            # 에피소드 프롬프트 생성
            generate_episode_prompt = self.generate_episode_prompt.format(
                random_topic_kor=random_topic_kor
            )
            messages = [
                {
                    "role": "system",
                    "content": generate_episode_prompt,
                }
            ]
            gen_image_prompt_kor = self.openai_create_nonstream(
                messages, model="gpt-4-0613", temperature=0.5
            )
            self.logger.info(f"""gen_image_prompt_kor : {gen_image_prompt_kor}""")

            translate_prompt = prompt._TRANSLATE_KOR_TO_ENG_PROMPT.format(
                kor_content=gen_image_prompt_kor
            )
            messages = [
                {
                    "role": "system",
                    "content": translate_prompt,
                }
            ]
            gen_image_prompt_eng = self.openai_create_nonstream(
                messages, model="gpt-4-0613", temperature=0.5
            )

            # gen_image_prompt_eng = self.papago_translate(
            #     gen_image_prompt_kor, "ko", "en"
            # )
            self.logger.info(f"""gen_image_prompt_eng : {gen_image_prompt_eng}""")

        if "kor" in self.instagram_id:
            gen_image_prompt_kor = prompt._GENERATE_KOR_IMAGE_PROMPT.format(
                random_topic_kor=random_topic_kor
            )
            gen_image_prompt_eng = None
            self.logger.info(f"""gen_image_prompt_kor : {gen_image_prompt_kor}""")

        self.logger.info("Generating Prompt Comoplete.")

        # 프롬프트 저장
        with open(os.path.join(src_path, "prompt.txt"), "w") as file:
            file.write(f"Random Topic Kor: {random_topic_kor}\n")
            file.write(f"Random Topic Eng: {random_topic_eng}\n")
            file.write(f"Generate Image Prompt Kor: {gen_image_prompt_kor}\n")
            if gen_image_prompt_eng:
                file.write(f"Generate Image Prompt Eng: {gen_image_prompt_eng}\n")

        self.logger.info("Save Prompt Comoplete.")

        src_prompt = {}
        src_prompt["random_topic_kor"] = random_topic_kor
        src_prompt["random_topic_eng"] = random_topic_eng
        src_prompt["gen_image_prompt_kor"] = gen_image_prompt_kor
        src_prompt["gen_image_prompt_eng"] = gen_image_prompt_eng
        return src_prompt

    def generate_image(self, gen_image_prompt_kor, src_path):
        image_url = self.openai_create_image(gen_image_prompt_kor)
        self.logger.info("Generating Image Comoplete.")

        response = requests.get(image_url)
        if response.status_code == 200:
            with open(os.path.join(src_path, "image.jpg"), "wb") as file:
                file.write(response.content)
            self.logger.info("Image Save Complete.")
        else:
            self.logger.info("Failed to download image.")

        self.logger.info("Save Image Comoplete.")

    def load_prompt(self, src_path):
        src_prompt = {}

        # 프롬프트 가져오기
        with open(os.path.join(src_path, "prompt.txt"), "r") as file:
            lines = file.readlines()

        # 각 변수에 내용 할당
        for line in lines:
            if line.startswith("Random Topic Kor:"):
                src_prompt["random_topic_kor"] = line.replace(
                    "Random Topic Kor:", ""
                ).strip()
            elif line.startswith("Random Topic Eng:"):
                src_prompt["random_topic_eng"] = line.replace(
                    "Random Topic Eng:", ""
                ).strip()
            elif line.startswith("Generate Image Prompt Kor:"):
                src_prompt["gen_image_prompt_kor"] = line.replace(
                    "Generate Image Prompt Kor:", ""
                ).strip()
            elif line.startswith("Generate Image Prompt Eng:"):
                src_prompt["gen_image_prompt_eng"] = line.replace(
                    "Generate Image Prompt Eng:", ""
                ).strip()

        return src_prompt

    def load_sources(self):
        # 계정 단위 폴더가 있는지 확인하고, 없으면 생성
        account_path = os.path.join(
            self.data_folder, f"src/auto_posting/{self.instagram_id}"
        )
        if not os.path.exists(account_path):
            os.makedirs(account_path)

        # 오늘 날짜 폴더가 있는지 확인하고, 없으면 생성
        current_date = datetime.now().strftime("%Y%m%d")
        src_path = os.path.abspath(os.path.join(account_path, current_date))
        if not os.path.exists(src_path):
            os.makedirs(src_path)

        # 프롬프트 유무 확인
        if os.path.exists(os.path.join(src_path, "prompt.txt")):
            src_prompt = self.load_prompt(src_path)
        else:
            # 프롬프트 생성
            src_prompt = self.generate_prompt(src_path)

        # 이미지 유무 확인
        if not os.path.exists(os.path.join(src_path, "image.jpg")):
            # 이미지 생성
            self.generate_image(src_prompt["gen_image_prompt_kor"], src_path)
        src_image = os.path.join(src_path, "image.jpg")

        # 게시글 유무 확인
        if os.path.exists(os.path.join(src_path, "posting_text.txt")):
            posting_text = ""
            with open(os.path.join(src_path, "posting_text.txt"), "r") as file:
                lines = file.readlines()

            for line in lines:
                posting_text += line
        else:
            # 게시글 생성
            if "dev" in self.instagram_id:
                posting_text = self.posting_text.format(
                    count_days=self.count_days(),
                    random_topic_kor=src_prompt["random_topic_kor"],
                    random_topic_eng=src_prompt["random_topic_eng"],
                    gen_image_prompt_kor=src_prompt["gen_image_prompt_kor"],
                    gen_image_prompt_eng=src_prompt["gen_image_prompt_eng"],
                )
            if "kor" in self.instagram_id:
                posting_text = self.posting_text.format(
                    count_days=self.count_days(),
                    random_topic_eng=src_prompt["random_topic_eng"],
                )
            with open(os.path.join(src_path, "posting_text.txt"), "w") as file:
                file.write(f"{posting_text}\n")

        return src_prompt, src_image, posting_text

    def posting(self, src_image, posting_text):
        self.logger.info("""Start posting.""")

        btn_plus = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div.x1iyjqo2.xh8yej3 > div:nth-child(7) > div > span > div > a > div""",
                ),
            ),
        )
        self.chromedriver.execute_script("arguments[0].click();", btn_plus)
        self.logger.info("Click btn_plus.")

        try:
            btn_post = self.chromedriver.find_element(
                By.CSS_SELECTOR,
                "span > div > div > div > div > a:nth-child(1)",
            )
            self.chromedriver.execute_script("arguments[0].click();", btn_post)
            self.logger.info("Click btn_post.")
        except TimeoutException:
            self.logger.warning("btn_post not found within timeout, continuing...")

        # 이미지 업로드 버튼
        btn_load = WebDriverWait(self.chromedriver, 10).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div.x6s0dn4.x78zum5.x5yr21d.xl56j7k.x1n2onr6.xh8yej3 > form > input""",
                ),
            ),
        )
        btn_load.send_keys(src_image)
        self.logger.info("Click btn_load.")
        time.sleep(1)

        # 문제 발생 예외처리
        try:
            status = WebDriverWait(self.chromedriver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div._ac7a"))
            )
            if status.text == "문제가 발생했습니다":
                return self.logger.info("Posting Problem Occured.")
        except:
            pass

        btn_next = WebDriverWait(self.chromedriver, 20).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div._ap97 > div > div > div > div._ac7b._ac7d > div > div""",
                ),
            ),
        )
        self.chromedriver.execute_script("arguments[0].click();", btn_next)
        self.logger.info("Click btn_next1.")
        time.sleep(1)

        btn_next = WebDriverWait(self.chromedriver, 20).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div._ap97 > div > div > div > div._ac7b._ac7d > div > div""",
                ),
            ),
        )
        self.chromedriver.execute_script("arguments[0].click();", btn_next)
        self.logger.info("Click btn_next2.")
        time.sleep(1)

        # 게시글 입력
        textbox = WebDriverWait(self.chromedriver, 60).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    """div.x6s0dn4.x78zum5.x1n2onr6.xh8yej3 > div""",
                ),
            ),
        )
        actions = ActionChains(auto_posting.chromedriver)
        actions.move_to_element(textbox).click().send_keys(
            posting_text, Keys.RETURN
        ).perform()

        btn_next = self.chromedriver.find_element(
            By.CSS_SELECTOR,
            """div._ac7b._ac7d > div > div""",
        )
        # 스크립트로 하면 안됨
        btn_next.click()
        time.sleep(5)

        # 포스팅 완료 여부 확인
        attempts = 0
        while attempts < 10:
            try:
                status = WebDriverWait(self.chromedriver, 10).until(
                    EC.presence_of_element_located(
                        (
                            By.CSS_SELECTOR,
                            "div._ac7a",
                        )
                    )
                )
                if status.text == "게시물이 공유되었습니다":
                    self.logger.info(f"""posting_text : {posting_text}""")
                    break
                else:
                    self.logger.info(f"Status not matched, retrying...{attempts} / 10")
                    time.sleep(1)
                    attempts += 1
            except Exception as e:
                self.logger.error("Error:", str(e))
                break

            self.logger.info("* Posting Complete.")

    def run(self):
        self.logger.info("* Start Auto Posting Process.")
        start_time = time.time()

        # 게시물 소스 로드
        src_prompt, src_image, posting_text = self.load_sources()

        # Main Process
        try:
            # chromedriver 생성
            self.init_chromedriver()

            # 로그인
            self.login()

            # 게시물 포스팅
            self.posting(src_image, posting_text)

            runtime = round(time.time() - start_time)
            runtime = (
                f"""{f"{runtime//60}min " if runtime//60>0  else ""}{runtime%60} sec."""
            )
            self.logger.info("""Complete Auto Posting Process.""")
            self.logger.info(
                f"""{datetime.now().strftime("%Y년 %m월 %d일 %p %I시 %M분")}"""
            )
            self.logger.info(
                f"""Topic : {src_prompt["random_topic_kor"]}({src_prompt["random_topic_eng"]})"""
            )
            self.logger.info(runtime)

            message = """게시물이 공유되었습니다.\n"""
            message += f"""{datetime.now().strftime("%Y.%m.%d %p %I:%M")}\n"""
            message += f"""계정 : {self.instagram_id}\n"""
            message += f"""주제 : {src_prompt["random_topic_kor"]}({src_prompt["random_topic_eng"]})"""
            self.send_kakao_message(message)

        except Exception:
            self.logger.error(f"{traceback.format_exc()}")

        finally:
            self.logger.info("* Quit Auto Posting Process.")
            if self.chromedriver:
                self.chromedriver.quit()
                self.logger.info("Quit Chromedriver.")


if __name__ == "__main__":
    ID = sys.argv[1]

    # AutoPosting 클래스의 인스턴스를 생성합니다.
    auto_posting = AutoPosting(ID)

    # 계정 유무 확인
    if auto_posting.check_account():
        # 자동 포스팅 프로세스를 실행합니다.
        auto_posting.run()
    else:
        auto_posting.logger.warning("Not Exist Account")
