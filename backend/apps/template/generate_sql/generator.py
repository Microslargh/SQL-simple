from apps.template.template import get_base_template
from common.core.config import settings


def _strip_legacy_domain_rules(text: str) -> str:
    if not text:
        return text
    legacy_markers = [
        "dws_cqs", "version_code", "dws_cgn_jq_zbval", "sys_unittitle",
        "中国广核集团有限公司（合并）", "并表", "存续", "压减=是口径", "法人户数",
    ]
    lines = text.splitlines()
    kept = [line for line in lines if not any(marker in line for marker in legacy_markers)]
    return "\n".join(kept)


def _sanitize_sql_template(template: dict) -> dict:
    if getattr(settings, "LEGACY_DOMAIN_RULES_ENABLED", False):
        return template
    sanitized = {}
    for key, value in template.items():
        if isinstance(value, str):
            sanitized[key] = _strip_legacy_domain_rules(value)
        else:
            sanitized[key] = value
    return sanitized


def get_sql_template():
    template = get_base_template()
    return _sanitize_sql_template(template['template']['sql'])
