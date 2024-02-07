_GENERATE_DEV_EPISODE_PROMPT = "{random_topic_kor}을 주제로 어떤 개발자든 공감하고 재미있어 할 수 있는 웃긴 에피소드를 짧은 문단으로 하나만 얘기해줘"

_GENERATE_KOR_IMAGE_PROMPT = "1990년대 대한민국에 있는 서정적인 모습의 {random_topic_kor} 외부 혹은 실내 사진 만들어줘"

_COMMENT_PROMPT_dev_meme121 = """Understand the following guidelines and Write a short and simple comment prasing a photo on Instagram.
- Avoid excessive compliments.
- Use a appropriate emoji."""

_COMMENT_PROMPT_purinpurin_store = """Instagram에서 귀여운 사진을 칭찬하는 짧은 의견을 다음 지침을 이해하고 작성해.
- 인사말을 포함한다
- 적절한 이모티콘 1개를 사용한다
- [ㅎㅎㅎ, 좋아요, 들렀다 갑니다, 구경하러 오세요, 제 피드도 놀러오세요] 이 중 1개를 활용한다.
- 매번 다르게 생성한다
- [금지 표현] : 발견"""

_TRANSLATE_KOR_TO_ENG_PROMPT = """아래 내용을 영어로 번역해줘

{kor_content}"""

_REPORTER_GREET_PROMPT = """너는 매일 아침 인스타그램 성장률을 분석하여 이메일을 보내고 있어.
예의 있는 말투로 다음 내용을 포함한 짧은 코멘트를 작성해.
- Alfy 이름을 언급한 자기 소개 한 문장
- AI기반 자동화 인스타봇으로 수행된 활동 기록을 기반으로 분석한 내용임.
- 하루를 응원하는 한 줄 코멘트.
* 줄바꿈을 적절히 사용하세요."""
