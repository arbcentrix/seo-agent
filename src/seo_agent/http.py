import requests

DEFAULT_UA = "ArbcentrixSEOAgent/1.0"


def session(user_agent: str = DEFAULT_UA) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    return s
