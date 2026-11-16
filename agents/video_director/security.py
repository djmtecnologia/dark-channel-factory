"""
Security and Sanitization Module — AI Video Director
Garante a proteção rigorosa de segredos, tokens e credenciais em logs e mensagens de erro.
"""
from __future__ import annotations

import re


def sanitize_secret(text: str) -> str:
    """
    Sanitiza credenciais, tokens, API keys, authorization headers, bearer tokens,
    query parameters e URLs autenticadas de mensagens de erro e logs.
    Preserva dados técnicos úteis para diagnóstico sem expor segredos.
    """
    if not text:
        return ""
    s = str(text)

    # 1. URLs com credenciais: http(s)://user:password@host (suporta @ e caracteres especiais na senha)
    s = re.sub(r'(https?://)([^:/\s]+):([^\s/]+)@', r'\1\2:***@', s)

    # 2. Query params de chaves e credenciais em URLs: ?key=..., &key=..., ?api_key=..., &token=...
    s = re.sub(
        r'([?&](?:key|api[_-]?key|apikey|token|secret|auth|password|passwd|pwd|access_token|refresh_token|id_token|client_secret|private_key|credential|x-api-key)=)[^& \t\n\r"\'<>]+',
        r'\1***',
        s,
        flags=re.IGNORECASE,
    )

    # 3. Headers com Bearer: Authorization: Bearer <token> ou Bearer <token> (suporta +, /, =, etc.)
    s = re.sub(r"""(Bearer\s+)[^\s,;'"<>]+""", r'\1***', s, flags=re.IGNORECASE)

    # 4. Outros headers de chave/token sem Bearer: x-api-key: <token>, api-key: <token>, etc.
    s = re.sub(r"""((?:x-api-key|api-key)\s*[:=]\s*)[^\s,;'"<>]+""", r'\1***', s, flags=re.IGNORECASE)
    # Authorization header com esquema (Basic, Token, Digest, ApiKey): Authorization: Basic <credentials>
    s = re.sub(
        r"""(authorization\s*[:=]\s*(?:Basic|Token|Digest|ApiKey)\s+)[^\s,;'"<>]+""",
        r'\1***',
        s,
        flags=re.IGNORECASE,
    )
    # Authorization header sem esquema explícito (quando NÃO é Bearer/Basic/Token/Digest/ApiKey)
    s = re.sub(
        r"""(authorization\s*[:=]\s*(?!(?:Bearer|Basic|Token|Digest|ApiKey)\s+))[^\s,;'"<>]+""",
        r'\1***',
        s,
        flags=re.IGNORECASE,
    )

    # 5. Formatos conhecidos de chaves de provedores de IA
    # OpenAI / DeepSeek: sk-... (ou sk-proj-..., sk-svcacct-...)
    s = re.sub(r'sk-[a-zA-Z0-9_\-]{12,}', 'sk-***', s)
    # Google Gemini API Keys: AIza...
    s = re.sub(r'AIza[0-9A-Za-z_\-]{20,}', 'AIza***', s)

    # 6. Atribuições chave-valor comuns:
    # 6a. Valores entre aspas (podem conter espaços, vírgulas, ponto-e-vírgula e caracteres especiais)
    s = re.sub(
        r'((?:api[_-]?key|apikey|token|secret|auth|password|passwd|pwd|access_token|refresh_token|client_secret|private_key)\s*[:=]\s*)(["\'])(?:\\.|(?!\2).)+\2',
        r'\1\2***\2',
        s,
        flags=re.IGNORECASE,
    )
    # 6b. Valores sem aspas
    s = re.sub(
        r"""((?:api[_-]?key|apikey|token|secret|auth|password|passwd|pwd|access_token|refresh_token|client_secret|private_key)\s*[:=]\s*)[^\s,;'"<>]+""",
        r'\1***',
        s,
        flags=re.IGNORECASE,
    )

    return s
