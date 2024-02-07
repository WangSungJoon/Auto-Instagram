import base64
import logging
import os
import re
import smtplib
import sys
import time
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import sqlalchemy
from dotenv import load_dotenv
from jinja2 import Template
from openai import OpenAI
from sqlalchemy import create_engine
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import SQLAlchemyError

import config
import prompt

load_dotenv()
MAX_RETRY = 5


class AutoReporter:
    def __init__(self, ID):
        self.script_path = os.path.dirname(os.path.abspath(__file__))
        self.logs_folder = os.path.join(self.script_path, "logs")
        self.data_folder = os.path.join(self.script_path, "data")
        self.instagram_id = ID
        self.db_config = {
            "drivername": os.getenv("_DB_DRIVERNAME"),
            "host": os.getenv("_DB_HOST"),
            "port": int(os.getenv("_DB_PORT")),
            "username": os.getenv("_DB_USERNAME"),
            "password": os.getenv("_DB_PASSWORD"),
            "database": os.getenv("_DB_DATABASE"),
        }

        self.from_email = os.getenv("_EMAIL_ACCOUNT")
        self.password = os.getenv("_EMAIL_PASSWORD")
        self.smtp_server = os.getenv("_EMAIL_SMTP")
        self.smtp_port = os.getenv("_EMAIL_PORT")

        self.SQLENGHINE = create_engine(URL.create(**self.db_config))
        self.client = OpenAI(api_key=os.getenv("_OPENAI_API_KEY"))
        self.logger = self.init_logger()
        self.logger.info("* Init AutoReporter")

    def init_logger(self, level=logging.INFO):
        account_folder_path = os.path.join(
            self.logs_folder, f"auto_reporter/{self.instagram_id}"
        )
        if not os.path.exists(account_folder_path):
            os.makedirs(account_folder_path)

        # 개별 로거 생성 및 설정
        logger = logging.getLogger("AutoReporterLogger")
        logger.setLevel(level)

        # 로그 포맷 정의
        formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

        # 로그 파일 경로 설정
        current_date = datetime.now().strftime("%Y%m%d")
        log_file_path = os.path.join(account_folder_path, f"{current_date}.log")

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

    def gpt_trimmer(self, answer):
        answer = answer.replace("\n", "<br>")
        answer = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", answer)
        answer = answer.replace("#", "")
        # answer = re.sub(r"####(.*?)<br>", r"<h4>\1<br>", answer)
        # answer = re.sub(r"###(.*?)<br>", r"<h3>\1<br>", answer)
        # answer = re.sub(r"##(.*?)<br>", r"<h2>\1<br>", answer)
        # answer = re.sub(r"##(.*?)<br>", r"<h1>\1<br>", answer)
        return answer

    def status_analysis(self):
        self.logger.info("Analizing Activity...")

        insert_query = f"""
            SELECT user_id, article, follower, follow, datetime FROM insta_status_log
            WHERE user_id='{self.instagram_id}'
            ORDER BY datetime
        """
        # 쿼리 실행
        data = self.execute_query(insert_query)

        # 데이터프레임 생성
        df = pd.DataFrame(
            data, columns=["user_id", "article", "follower", "follow", "datetime"]
        )
        df["datetime"] = pd.to_datetime(df["datetime"])  # 날짜 형식으로 변환

        df.set_index("datetime", inplace=True)

        # 전체 성장률 계산
        total_growth_rate = (
            (df["follower"].iloc[-1] - df["follower"].iloc[0]) / df["follower"].iloc[0]
        ) * 100

        # 오늘 팔로워 증가 수 및 어제 대비 증가율 계산
        today_growth = df["follower"].iloc[-1] - df["follower"].iloc[-2]
        yesterday_growth_rate = (
            (df["follower"].iloc[-2] - df["follower"].iloc[-3])
            / df["follower"].iloc[-3]
        ) * 100

        # 이번주 팔로워 증가 수 및 지난주 대비 증가율 계산
        this_week_growth = df["follower"].resample("W-Mon").last().diff().iloc[-1]
        last_week_growth_rate = (
            (df["follower"].resample("W-Mon").last().diff().iloc[-1])
            / df["follower"].resample("W-Mon").last().iloc[-2]
        ) * 100

        # 이번달 팔로워 증가 수 및 지난달 대비 증가율 계산
        this_month_growth = df["follower"].resample("ME").last().diff().iloc[-1]
        last_month_growth_rate = (
            (df["follower"].resample("ME").last().diff().iloc[-1])
            / df["follower"].resample("ME").last().iloc[-2]
        ) * 100

        # 하루, 주간, 월간 평균 팔로워 수 계산
        daily_avg_growth = df["follower"].diff().mean()
        weekly_avg_growth = df["follower"].resample("W").last().diff().mean()
        monthly_avg_growth = df["follower"].resample("ME").last().diff().mean()

        # 내일, 다음주, 다음달 예측 팔로워 수 계산
        tomorrow_prediction = df["follower"].iloc[-1] + daily_avg_growth
        next_week_prediction = df["follower"].iloc[-1] + weekly_avg_growth
        next_month_prediction = df["follower"].iloc[-1] + monthly_avg_growth

        # 분석 멘트 출력
        analysis_prompt = "아래 정보를 활용해서 정확하고 깔끔한 분석 리포트를 작성하고, 적절한 이모티콘 활용해\n\n"
        analysis_prompt += f"{self.instagram_id} 리포트\n"
        analysis_prompt += f"게시글: {df['article'].iloc[-1]}개 / 팔로워: {df['follower'].iloc[-1]}개 / 팔로우: {df['follow'].iloc[-1]}개\n"
        analysis_prompt += f"현재까지 총 성장률: {total_growth_rate:.2f}% {'상승' if total_growth_rate > 0 else '하락'}\n"
        analysis_prompt += f"어제 팔로워 수: {int(today_growth)}개 {'증가' if today_growth > 0 else '감소'}\n"
        analysis_prompt += f"어제 대비 증가율: {yesterday_growth_rate:.2f}% {'상승' if yesterday_growth_rate > 0 else '하락'}\n\n"
        analysis_prompt += f"이번주 팔로워 수: {int(this_week_growth)}개 {'증가' if this_week_growth > 0 else '감소'}\n"
        analysis_prompt += f"지난주 대비 증가율: {last_week_growth_rate:.2f}% {'상승' if last_week_growth_rate > 0 else '하락'}\n\n"
        analysis_prompt += f"이번달 팔로워 수: {int(this_month_growth)}개 {'증가' if this_month_growth > 0 else '감소'}\n"
        analysis_prompt += f"지난달 대비 증가율: {last_month_growth_rate:.2f}% {'상승' if last_month_growth_rate > 0 else '하락'}\n\n"
        analysis_prompt += f"하루 평균 팔로워 수: {daily_avg_growth:.2f}개 {'증가' if daily_avg_growth > 0 else '감소'}\n"
        analysis_prompt += f"주간 평균 팔로워 수: {weekly_avg_growth:.2f}개 {'증가' if weekly_avg_growth > 0 else '감소'}\n"
        analysis_prompt += f"월간 평균 팔로워 수: {monthly_avg_growth:.2f}개 {'증가' if monthly_avg_growth > 0 else '감소'}\n\n"
        analysis_prompt += f"내일 예측 팔로워 수: {int(tomorrow_prediction)}개 {'증가' if tomorrow_prediction > 0 else '감소'}\n"
        analysis_prompt += f"다음주 예측 팔로워 수: {int(next_week_prediction)}개 {'증가' if next_week_prediction > 0 else '감소'}\n"
        analysis_prompt += f"다음달 예측 팔로워 수: {int(next_month_prediction)}개 {'증가' if next_month_prediction > 0 else '감소'}\n\n"

        messages = [{"role": "system", "name": "Alfy", "content": analysis_prompt}]
        analysis_result = self.openai_create_nonstream(
            messages, "gpt-4-turbo-preview", 0.5
        )
        analysis_result = self.gpt_trimmer(analysis_result)

        self.logger.info("Complete Analizing Activity")
        return analysis_result

    def email_src_maker(self, src_path):
        # greet 생성
        greet_prompt = prompt._REPORTER_GREET_PROMPT.format()
        messages = [{"role": "system", "name": "Alfy", "content": greet_prompt}]
        content_greet = self.openai_create_nonstream(
            messages, "gpt-4-turbo-preview", 0.5
        )
        content_greet = self.gpt_trimmer(content_greet)
        self.logger.info(f"""Greet : {content_greet}""")

        # content_body 생성
        content_body = self.status_analysis()
        self.logger.info(f"""Analysis Result : {content_body}""")

        # status_chart 생성
        chart_src = self.generate_status_chart()

        # activity_chart_src = self.generate_activity_chart()

        with open(
            os.path.join(src_path, "email_html.html"), "w", encoding="utf-8"
        ) as output_file:
            with open("static/j2_template.html") as template_file:
                j2_template = Template(template_file.read())
                email_html_src = j2_template.render(
                    content_greet=content_greet,
                    chart_src=chart_src,
                    content_body=content_body,
                )
                output_file.write(email_html_src)

        return email_html_src

    def generate_status_chart(self):
        """
        계정 현황 차트를 생성하는 함수
        """

        insert_query = f"""
            SELECT user_id, article, follower, follow, datetime FROM insta_status_log
            WHERE user_id='{self.instagram_id}'
            ORDER BY datetime
        """
        data = self.execute_query(insert_query)

        status_columns = ["user_id", "게시글", "팔로워", "팔로우", "날짜"]
        df = pd.DataFrame(data, columns=status_columns)
        df["datetime"] = pd.to_datetime(df["날짜"])
        df.set_index("datetime", inplace=True)

        fig = go.Figure()

        # 각 trace를 추가
        # 눈에 잘 들어오는 색으로 변경
        colors = ["#33FF4C", "#FF5733", "#337DFF"]
        for col, color in zip(["게시글", "팔로워", "팔로우"], colors):
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df[col],
                    mode="lines+markers",
                    name=col.capitalize(),
                    line=dict(color=color, width=2),
                    marker=dict(color=color, size=8),
                )
            )
        dict(size=20, color="white", family="Arial")
        # 마지막 날짜 항목에만 값 표시
        for col, color in zip(["게시글", "팔로워", "팔로우"], colors):
            last_date = df.index[-1]
            last_value = df.loc[last_date, col]
            fig.add_trace(
                go.Scatter(
                    x=[last_date],
                    y=[last_value],
                    mode="markers+text",
                    text=[last_value],
                    marker=dict(
                        size=10, color=color, line=dict(color="#ffffff", width=2)
                    ),
                    showlegend=False,
                    textposition="top center",
                )
            )

        # Hover 정보 설정
        fig.update_traces(hovertemplate="%{x}<br>%{y:f}")

        # 레이아웃 및 스타일 조정
        fig.update_layout(
            title=dict(
                text=self.instagram_id,
                font=dict(size=20, color="white", family="Arial"),
            ),  # 제목 스타일 조정
            width=800,  # 가로 크기 설정
            height=400,  # 세로 크기 설정
            template="plotly_dark",  # 어두운 템플릿 사용
            xaxis=dict(
                showline=True,
                showgrid=False,
                linecolor="#ffffff",
                linewidth=2,
                ticks="outside",
                tickcolor="#ffffff",
            ),
            yaxis=dict(
                showline=True,
                showgrid=True,
                gridcolor="rgba(255, 255, 255, 0.1)",
                linecolor="#ffffff",
                linewidth=2,
                ticks="outside",
                tickcolor="#ffffff",
            ),
            paper_bgcolor="rgba(0, 0, 0, 0)",
            plot_bgcolor="rgba(0, 0, 0, 0)",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            margin=dict(l=10, r=10, t=60, b=10),
        )

        # 선 스타일 및 두께 조정
        fig.update_traces(line=dict(width=2))
        self.logger.info("""Generating Status Chart Complete.""")

        # return fig.to_html(full_html=False)

        image_bytes = pio.to_image(fig, format="jpg")
        encoded_image = base64.b64encode(image_bytes).decode("utf-8")
        return f'<img src="data:image/png;base64,{encoded_image}" alt="Plotly Figure">'

    def generate_activity_chart(self):
        """
        오토봇 활동 차트를 생성하는 함수
        """

        # 데이터베이스에서 데이터 가져오기
        insert_query = f"""
            SELECT user_id, access, follow, likes, comment, DM, datetime FROM auto_manager_log
            WHERE user_id='{self.instagram_id}'
            ORDER BY datetime
        """
        # 쿼리 실행
        data = self.execute_query(insert_query)

        # 데이터 프레임 생성
        activity_columns = [
            "user_id",
            "access",
            "follow",
            "likes",
            "comment",
            "DM",
            "datetime",
        ]
        df = pd.DataFrame(data, columns=activity_columns)
        df["datetime"] = pd.to_datetime(df["datetime"])

        # 날짜별 활동량 계산
        result = (
            df.groupby(df["datetime"].dt.date)
            .agg(
                {
                    "access": "sum",
                    "follow": "sum",
                    "likes": "sum",
                    "comment": "sum",
                    "DM": "sum",
                }
            )
            .to_dict(orient="index")
        )

        dates = list(result.keys())
        activity_cols = ["access", "follow", "likes", "comment", "DM"]
        activity_names = ["접근", "팔로우", "좋아요", "댓글", "DM"]

        # 그래프 생성
        fig = go.Figure()

        # 각 trace를 추가
        for col, name in zip(activity_cols, activity_names):
            fig.add_trace(
                go.Bar(x=dates, y=[result[date][col] for date in dates], name=name)
            )

        # 레이아웃 및 스타일 조정
        fig.update_layout(
            title=dict(
                text="Alfy 활동 분석", font=dict(size=20, color="white", family="Arial")
            ),
            xaxis_title=dict(
                text="날짜", font=dict(size=20, color="white", family="Arial")
            ),
            yaxis_title=dict(
                text="횟수", font=dict(size=20, color="white", family="Arial")
            ),
            width=800,  # 가로 크기 설정
            height=400,  # 세로 크기 설정
            template="plotly_dark",
            legend=dict(
                orientation="h",
                yanchor="top",
                y=1.02,
                xanchor="right",
                x=1,
                font=dict(size=16, color="white", family="Arial"),
            ),
        )

        # Hover 정보 설정
        fig.update_traces(hovertemplate="%{x}<br>%{y:f}")
        self.logger.info("""Generating Status Chart Complete.""")

        # return fig.to_html(full_html=False)

        image_bytes = pio.to_image(fig, format="jpg")
        encoded_image = base64.b64encode(image_bytes).decode("utf-8")
        return f'<img src="data:image/png;base64,{encoded_image}" alt="Plotly Figure">'

    def load_sources(self):
        account_path = os.path.join(
            self.data_folder, f"src/auto_reporter/{self.instagram_id}"
        )
        if not os.path.exists(account_path):
            os.makedirs(account_path)

        # 오늘 날짜 폴더가 있는지 확인하고, 없으면 생성
        current_date = datetime.now().strftime("%Y%m%d")
        src_path = os.path.abspath(os.path.join(account_path, current_date))
        if not os.path.exists(src_path):
            os.makedirs(src_path)

        # email_subject 생성
        email_subject = f"""[{datetime.now().strftime("%Y-%m-%d")}] 오늘 아침에 분석한 {self.instagram_id} 리포트입니다!"""

        # 이메일 내용 유무 확인
        if os.path.exists(os.path.join(src_path, "email_html.html")):
            # email_html_src 가져오기
            with open(
                os.path.join(src_path, "email_html.html"), "r", encoding="utf-8"
            ) as file:
                email_html_src = file.read()
        else:
            email_html_src = self.email_src_maker(src_path)

        return email_subject, email_html_src

    def send_email(self, to_email, subject, content):
        msg = MIMEMultipart()
        msg["From"] = self.from_email
        msg["To"] = ", ".join(to_email)
        msg["Subject"] = subject
        msg.attach(MIMEText(content, "html"))

        with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
            server.starttls()
            server.login(self.from_email, self.password)
            server.sendmail(
                self.from_email,
                to_email,
                msg.as_string(),
            )

        self.logger.info("Email Send Complete.")

    def run(self):
        try:
            start_time = time.time()

            # 이메일 내용 로드
            emil_subject, email_html_src = self.load_sources()

            # 이메일 발송
            self.send_email(
                config._TO_EMAIL_DICT[self.instagram_id], emil_subject, email_html_src
            )

            self.logger.info("* Complete Auto Reporting Process.")
            runtime = round(time.time() - start_time)
            runtime = f"""Runtime : {f"{runtime//60} min " if runtime//60>0  else ""}{runtime%60} sec."""
            self.logger.info(runtime)
        except Exception:
            self.logger.error("""알 수 없는 에러가 발생하였습니다.""")
            self.logger.error(f"{traceback.format_exc()}")


if __name__ == "__main__":
    ID = sys.argv[1]
    activity_reporter = AutoReporter(ID)

    activity_reporter.run()
