# News Radar Mobile Cloud

휴대폰 사용을 위해 Streamlit Community Cloud 배포에 맞춘 버전입니다.

## 파일
- `streamlit_app.py`: 앱 본체
- `requirements.txt`: Python 패키지
- `.streamlit/config.toml`: Streamlit 설정
- `secrets.toml.example`: 배포용 네이버 API Secrets 예시
- `.gitignore`: 비밀키가 GitHub에 올라가지 않도록 설정

## Streamlit Cloud Secrets
배포 화면의 Advanced settings > Secrets에 아래 형식으로 입력하세요.

```toml
NAVER_CLIENT_ID = "실제_Client_ID"
NAVER_CLIENT_SECRET = "실제_Client_Secret"
```

실제 키가 들어간 `secrets.toml`은 GitHub에 업로드하지 마세요.

## 로컬 테스트
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```
