import re
import unicodedata

URL_RE = re.compile(r'https?://\S+|www\.\S+')
HANDLE_RE = re.compile(r'[@#]\w+')
EMOJI_RE = re.compile(r'[\U00010000-\U0010ffff]')  # filtra pictogramas
WS_RE = re.compile(r'\s+')


def normalize_for_embedding(text: str) -> str:
    if not text:
        return ""
    # 1) minúsculas
    t = text.lower()
    # 2) quitar urls, handles/hashtags y emojis
    t = URL_RE.sub(" ", t)
    t = HANDLE_RE.sub(" ", t)
    t = EMOJI_RE.sub(" ", t)
    # 3) normalizar comillas y guiones a ASCII
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode('ascii')
    # 4) colapsar espacios
    t = WS_RE.sub(' ', t).strip()
    return t
