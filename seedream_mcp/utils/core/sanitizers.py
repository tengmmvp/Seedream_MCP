"""敏感数据脱敏与文本净化：键值吸收、控制字符剥离、输出截断与 URL 轻量路径。"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar, cast

# 上游错误 message 片段拼入异常文案前的截断上限，防止超大错误体形成巨型日志行；
# 重试日志脱敏时复用为宽截断上限，日志保留原文便于排障。
UPSTREAM_MESSAGE_FRAGMENT_LIMIT = 8 * 1024


def _normalize_non_str_message(value: Any) -> str:
    """将非字符串 message 归一化为文本：dict/list 以 JSON 序列化，其余取 str。

    JSON 序列化的引号与转义形态仍被键值脱敏的引号变体与转义容忍覆盖，凭据不借
    dict/list 形态穿透；超深嵌套触发递归上限时逐级回退到类型占位符，
    RecursionError 不外逃。
    """
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError, RecursionError):
            try:
                return str(value)
            except RecursionError:
                return f"<unprintable:{type(value).__name__}>"
    return str(value)


def normalize_message_text(value: Any) -> str:
    """将任意形态的错误 message 分量归一化为文本，字符串原样返回。

    净化出口先经本函数归一化再脱敏，凭据不借 dict/list 形态穿透文本与结构化输出。
    """
    return value if isinstance(value, str) else _normalize_non_str_message(value)


# 截断标记的头部长度模式，补偿截断前剥离旧标记保持产物单标记。
_TRUNCATION_MARKER_PREFIX = re.compile(r"^<truncated:\d+ chars> ")


def _truncate_text_with_marker(text: str, limit: int, *, reported_length: int | None = None) -> str:
    """截断文本并标注长度，产物总长不超过 limit。

    总长受控使截断产物再次进入截断时不再超限；limit 连标记加省略号都装不下时
    退化为纯截断，不产出残缺标记。reported_length 覆盖标记申报的长度，供
    补偿截断沿用原始输入的长度申报。
    """
    length = len(text) if reported_length is None else reported_length
    marker = f"<truncated:{length} chars> "
    if limit <= len(marker) + len("..."):
        return text[:limit]
    keep = limit - len(marker) - len("...")
    return f"{marker}{text[:keep]}..."


def _retruncate_with_single_marker(text: str, limit: int, reported_length: int) -> str:
    """剥离旧截断标记后按申报长度重拼单标记截断，补偿脱敏膨胀。

    错误文本与 URL 数据两条通道的补偿共用，产物恒单标记。
    """
    stripped = _TRUNCATION_MARKER_PREFIX.sub("", text, count=1)
    return _truncate_text_with_marker(stripped, limit, reported_length=reported_length)


def truncate_upstream_message_fragment(value: Any) -> str:
    """归一化并截断上游错误 message 片段至 8KB 内，超长时保留前缀并标注原长度。

    handle_api_error 拼入上游 error/message 字段前统一经本函数处理，防止 dict 形态
    经 repr 插值绕过键值脱敏、超大错误体随异常进入日志；io_sse 的请求级错误事件
    message 拼装共用本函数。
    """
    text = normalize_message_text(value)
    if len(text) > UPSTREAM_MESSAGE_FRAGMENT_LIMIT:
        return _truncate_text_with_marker(text, UPSTREAM_MESSAGE_FRAGMENT_LIMIT)
    return text


# _truncate_value_for_output 的默认截断上限。
_VALUE_OUTPUT_LIMIT = 200
# 错误消息序列化时的长度上限：避免上游回显的长片段进入用户可见输出或结构化响应。
# 与 DATA_OUTPUT_LIMIT 一并为跨模块共享的净化调参契约。
MESSAGE_OUTPUT_LIMIT = 500
# dict/list 元素数超过此值即跳过长度估计直接给摘要，超大容器不进入逐元素遍历。
_CONTAINER_REPR_ELEMENT_LIMIT = 50
# 容器嵌套深度上限：超过后长度估计返回 None，截断走类型占位符，与 RecursionError
# 兜底口径一致。
_CONTAINER_REPR_DEPTH_LIMIT = 1000
# 非 str/bytes 叶子元素的长度估计按小常数计入，不为判长物化其 repr。
_CONTAINER_LEAF_LENGTH_ESTIMATE = 16
# 容器单元素在 repr 形态中的标点开销按固定值计入。
_CONTAINER_ELEMENT_OVERHEAD = 6


def _container_summary(value: Any) -> str:
    """返回 dict/list 的元素数摘要，用于超限或元素过多时替代完整 repr。"""
    if isinstance(value, dict):
        return f"<truncated:dict, {len(value)} keys>"
    return f"<truncated:list, {len(value)} items>"


def _estimate_container_output_length(value: Any, limit: int) -> int | None:
    """迭代估计 dict/list 的输出长度，供截断判长使用，不物化完整 repr。

    str/bytes 键与元素按 len 加单元素标点开销计入，嵌套容器求和，其余元素按固定
    小常数计入；total 超过 limit 即提前返回，超限后的精确值无意义。显式栈配 id
    判重终止循环引用展开，嵌套深度超过 _CONTAINER_REPR_DEPTH_LIMIT 返回 None，
    由调用方以类型占位符兜底。
    """
    total = 0
    seen: set[int] = {id(value)}
    pending: list[tuple[Any, int]] = [(value, 1)]

    def leaf_length(item: Any) -> int:
        if isinstance(item, (str, bytes)):
            return len(item) + _CONTAINER_ELEMENT_OVERHEAD
        return _CONTAINER_LEAF_LENGTH_ESTIMATE

    def account_item(item: Any, depth: int) -> None:
        nonlocal total
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                total += _CONTAINER_LEAF_LENGTH_ESTIMATE
                return
            seen.add(id(item))
            pending.append((item, depth + 1))
            return
        total += leaf_length(item)

    while pending:
        node, depth = pending.pop()
        if depth > _CONTAINER_REPR_DEPTH_LIMIT:
            return None
        if isinstance(node, dict):
            for key, item in node.items():
                total += leaf_length(key)
                account_item(item, depth)
                if total > limit:
                    return total
        else:
            for item in node:
                account_item(item, depth)
                if total > limit:
                    return total
    return total


def _truncate_value_for_output(value: Any, limit: int = _VALUE_OUTPUT_LIMIT) -> Any:
    """截断过长的异常 value，防止 data URI、大字典等撑爆日志或结构化响应。

    - 字符串超限：保留前 ``limit`` 字符并标注原长度。
    - dict/list 元素过多或估计长度超限：收敛为元素数摘要。
    - dict/list 嵌套超深：保留类型占位符。
    - None 或未超限：原样返回。
    """
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return _truncate_text_with_marker(value, limit)
    if isinstance(value, (dict, list)):
        if len(value) > _CONTAINER_REPR_ELEMENT_LIMIT:
            return _container_summary(value)
        estimated = _estimate_container_output_length(value, limit=limit)
        if estimated is None:
            return f"<{type(value).__name__}>"
        if estimated <= limit:
            return value
        return _container_summary(value)
    return value


# 敏感字段关键词：键名等于关键词或按分隔符切分后任一段等于关键词即视为敏感，
# 输出以 *** 脱敏，段匹配避免短词 key 误吞 monkey、keyboard 一类普通键名。
_SENSITIVE_KEY_KEYWORDS = (
    "key",
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "auth",
    "cookie",
    "session",
    "jwt",
    "assertion",
    "signature",
    "nonce",
    "saml",
)

# 高确信度敏感词：直接子串匹配，覆盖 x-authorization、my-apikey、privatekey 等
# 连字符或无分隔变体，camelCase 归一化小写后同样命中。
_SENSITIVE_KEY_SUBSTRINGS = (
    "authorization",
    "apikey",
    "privatekey",
    "sshkey",
    "secretkey",
    "accesskey",
    "sessionkey",
    "authkey",
    "accesstoken",
    "authtoken",
    "refreshtoken",
    "sessiontoken",
    "apitoken",
    "clientsecret",
    "apisecret",
    "signingsecret",
    "appsecret",
)

# 敏感关键词的段集合与键名切分模式：集合由关键词清单派生保持单一来源，四种分隔
# 符经预编译字符类一次切分，供 is_sensitive_key 热路径成员判断使用。
_SENSITIVE_KEY_KEYWORD_SET = frozenset(_SENSITIVE_KEY_KEYWORDS)
_KEY_SEGMENT_SPLIT_PATTERN = re.compile(r"[_\- .]")


# Bearer 鉴权头令牌模式：上游错误体回显鉴权头时据此剥离令牌，防止其进入结构化输出。
_BEARER_TOKEN_PATTERN = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)

# 不参与交替组直接派生的短词：key 单独出现特异性不足，仅保留受限复合分支覆盖
# 敏感形态；auth 由 (?<!\w) 左侧断言与键后分隔符要求排除误吞后经独立分支覆盖。
_SENSITIVE_KEYVALUE_AMBIGUOUS_KEYWORDS = frozenset({"key", "auth"})

# key/auth 受限复合分支的前缀族：带分隔符的 X_key/X_auth 仅在前缀属于本族时命中，
# 口径差异见 is_sensitive_key。封闭字面交替，回溯保持线性。
_SENSITIVE_KEYVALUE_COMPOUND_PREFIXES = (
    "access",
    "ssh",
    "gpg",
    "encryption",
    "user",
    "public",
    "private",
    "auth",
    "client",
    "api",
    "signing",
    "app",
    "session",
    "secret",
)

# 键名续段：以 . 、- 或 _ 起头、后接不含分隔符的词字符段。续段边界唯一确定，
# 失败回溯随总长线性，避免嵌套量词的指数级回溯。
_SENSITIVE_KEYVALUE_KEY_SUFFIX = r"(?:[._-][^\W_.-]+)*"

# 敏感键名交替组：keyvalue 裸值模式的键匹配与值吸收的停止前瞻共用。分支由关键词
# 清单与前缀族派生，两路径口径以 is_sensitive_key 为单点。键命中要求紧跟分隔符
# 与值，max_tokens 等普通词形不受影响；新增敏感词只需扩展清单或前缀族。各分支为
# 字面交替，失败回溯随总长线性。
_SENSITIVE_KEYVALUE_KEYS = (
    "|".join(
        keyword + _SENSITIVE_KEYVALUE_KEY_SUFFIX
        for keyword in (*_SENSITIVE_KEY_SUBSTRINGS, *_SENSITIVE_KEY_KEYWORDS)
        if keyword not in _SENSITIVE_KEYVALUE_AMBIGUOUS_KEYWORDS
    )
    + r"|api[-_. ]?key"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
    + r"|(?:access|auth|refresh|session|api)[-_.]?token"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
    + r"|(?:client|api|signing|app)[-_.]?secret"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
    + r"|(?:"
    + "|".join(_SENSITIVE_KEYVALUE_COMPOUND_PREFIXES)
    + r")[-_. ]key"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
    + r"|(?:"
    + "|".join(prefix for prefix in _SENSITIVE_KEYVALUE_COMPOUND_PREFIXES if prefix != "auth")
    + r")[-_. ]auth"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
    + r"|(?<!\w)auth"
    + _SENSITIVE_KEYVALUE_KEY_SUFFIX
)

# 键值分隔符与值吸收共用的空白字符类：ASCII 空白加 Unicode 空白，封堵借 Unicode
# 空白分隔的绕过形态；控制空白成员在压平前的首轮匹配中承接「冒号加换行」形态，
# 压平后空转，保持类自身完备。
_KEYVALUE_WHITESPACE_CLASS = (
    r"[\t\n\r \x0b\x0c\x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
)

# 控制空白字符类：作为分隔符交替组的独占分支，覆盖无冒号等号的「键后直接跟
# 控制空白再跟值」形态，只在压平前的首轮键值匹配中存在。成员为全部 C0 与 NEL
# 中 isspace 为真的字符，含 \x1c-\x1f 的 FS/GS/RS/US；这些字符压平后仅成普通
# 空格，不构成冒号等号类分隔符，遗漏时凭据在两条匹配轮次中都存活。
_KEYVALUE_CONTROL_WHITESPACE_CLASS = r"[\t\n\r\x0b\x0c\x1c-\x1f\x85]"

# 键值分隔符交替组：字面转义族覆盖 \n、\uXXXX 一类转义序列，json.dumps 与 repr
# 转义后的键值凭据仍被命中；冒号等号族容忍可选反斜杠前缀；控制空白分支经尾部
# 前瞻只在空白串尾命中。冒号等号族的可选反斜杠与各转义族共用首字符，各分支在
# 首两字符上互斥，失败回溯随总长线性。
_SENSITIVE_KEYVALUE_ALT = (
    r"(?:\\[nrtf]"
    r"|\\u[0-9a-fA-F]{4}"
    r"|\\x[0-9a-fA-F]{2}"
    r"|\\?[:：﹕=＝]"
    r"|" + _KEYVALUE_CONTROL_WHITESPACE_CLASS + r"(?!" + _KEYVALUE_CONTROL_WHITESPACE_CLASS + r"))"
)

# 键值分隔符：键名后允许至多两段「引号 + 空白」组合，引号前容忍可选反斜杠，引号
# 与分隔符字符均含全角变体，覆盖 JSON/Python repr 回显、json.dumps 转义产物与
# 「引号-空白-引号」形态；转义族与控制空白独占分支见 _SENSITIVE_KEYVALUE_ALT，
# 空白段取 _KEYVALUE_WHITESPACE_CLASS。引号与空白组成计数受限的单一非捕获组、
# 各分支有限交替，失败回溯随总长线性。
_SENSITIVE_KEYVALUE_SEPARATOR = (
    _KEYVALUE_WHITESPACE_CLASS
    + r"*(?:\\?['\"＂＇]"
    + _KEYVALUE_WHITESPACE_CLASS
    + r"*){0,2}"
    + _SENSITIVE_KEYVALUE_ALT
    + _KEYVALUE_WHITESPACE_CLASS
    + r"*"
)

# 值吸收的停止前瞻形态：可选前导引号 + 任意键名 + 分隔符，不限于敏感词；下一个
# 词呈现键名加分隔符结构时值吸收停止，非敏感键值对作为独立键值对保留原文。
_SENSITIVE_KEYVALUE_ANY_KEY = r"['\"]?[\w-]+" + _SENSITIVE_KEYVALUE_SEPARATOR

# 敏感键值裸值模式：敏感键名后跟分隔符与值时剥离值部分，覆盖 api-key=xxx、
# Authorization: Basic xxx、Cookie: SESSIONID=vvv 等上游错误体回显形态。值吸收
# 贪婪多词、在下一个键名加分隔符形态前停止，多词凭据整体剥离，相邻非敏感键值对
# 保留独立形态与括号配平。已知误吞面：Cookie: 后的整段文本与 next token: <eos>
# 一类普通键值同样被吞，敏感键后的多词值无法与非敏感尾词可靠区分，按 fail-closed
# 方向宁多脱不漏凭据。
_SENSITIVE_KEYVALUE_PATTERN = re.compile(
    r"(?i)("
    + _SENSITIVE_KEYVALUE_KEYS
    + r")("
    + _SENSITIVE_KEYVALUE_SEPARATOR
    + r")\S+"
    + r"(?:"
    + _KEYVALUE_WHITESPACE_CLASS
    + r"+(?!"
    + _SENSITIVE_KEYVALUE_ANY_KEY
    + r")\S+)*"
)

# 控制字符模式：C0 控制字符、DEL、NEL（U+0085）与行/段分隔符（U+2028/U+2029）
# 逐字符压平为空格，防止经日志与结构化输出注入伪造行，替换为空格保留词边界。
# logs 的日志消息 patcher 共用本常量，两模块的控制字符口径单一来源。
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")

# 零宽不可见字符：ZWSP、ZWNJ、BOM 与软连字符。键值匹配前整体移除以还原真实键名，
# 替换为空串而非空格，避免把键名切成两段导致脱敏失效。
_INVISIBLE_CHARS_PATTERN = re.compile(r"[\u200b\u200c\ufeff\u00ad]")

# URL userinfo 剥离模式：http(s) URL 携带 user:pass@ 凭据时剥去 userinfo，防止
# 原值回显把凭据送进结构化输出与用户可见文本。密码含 @ 时贪婪匹配取区间内最后
# 一个 @ 定边界，user:p@ss@example.com 剥净不残留；协议限定 http(s) 且要求词
# 边界，不误伤 mailto:user@host。
_URL_USERINFO_PATTERN = re.compile(r"(?i)\b((?:https?)://)[^\s/?#]+@")


_SanitizedValue = TypeVar("_SanitizedValue")


def _sanitize_output_string(value: _SanitizedValue) -> _SanitizedValue:
    """对字符串值剥离零宽字符、敏感键值裸值、Bearer 令牌与 URL userinfo，非字符串原样返回。

    message 与 details/value/response_data 等输出字段共用此净化，防护口径一致。
    处理次序：零宽字符先行移除以还原真实键名，键值匹配在控制字符压平前后各执行
    一次，分别覆盖真实换行分隔形态与转义产物等其余形态，末尾剥 Bearer 令牌与
    URL userinfo。
    """
    if not isinstance(value, str):
        return value
    redacted = _INVISIBLE_CHARS_PATTERN.sub("", value)
    redacted = _SENSITIVE_KEYVALUE_PATTERN.sub(r"\1\2***", redacted)
    redacted = CONTROL_CHARS_PATTERN.sub(" ", redacted)
    redacted = _SENSITIVE_KEYVALUE_PATTERN.sub(r"\1\2***", redacted)
    redacted = _BEARER_TOKEN_PATTERN.sub(r"\1***", redacted)
    return cast("_SanitizedValue", _URL_USERINFO_PATTERN.sub(r"\1", redacted))


def _sanitize_message_for_output(value: Any, limit: int = MESSAGE_OUTPUT_LIMIT) -> str:
    """对异常 message 先截断再剥离敏感片段，供 format_error_for_user 输出净化。

    非字符串先归一化为文本再进管线，dict 形态不借 str/repr 穿透。
    """
    return sanitize_error_text(normalize_message_text(value), limit=limit)


def sanitize_error_text(
    message: _SanitizedValue, limit: int = MESSAGE_OUTPUT_LIMIT
) -> _SanitizedValue:
    """对上游错误文本先截断再剥离敏感片段与控制字符，供全部用户可见输出路径共用。

    先截断使脱敏正则的工作长度受 limit 约束，是对抗超长构造输入的纵深兜底；丢弃
    段凭据随截断消失，保留段凭据被脱敏剥离，截断点落在键名中间时值已一并丢弃，
    两个方向都不残留。脱敏把短值替换为 *** 可能使产物超过 limit，剥旧标记重拼
    单标记补偿一轮即收敛：补偿截断后的文本已脱敏，再脱敏恒等；异常路径外的
    结果数据出口——SSE 失败事件、响应 data 项的 error 字段、并行聚合消息、
    自动保存 error——统一经本函数收敛为同一防护口径。非字符串原样返回。
    """
    if not isinstance(message, str):
        return message
    sanitized = _sanitize_output_string(_truncate_value_for_output(message, limit=limit))
    if len(sanitized) > limit:
        sanitized = _sanitize_output_string(
            _retruncate_with_single_marker(sanitized, limit, len(message))
        )
    if len(sanitized) > limit:
        sanitized = sanitized[:limit]
    return cast("_SanitizedValue", sanitized)


# 数据字段序列化的防御性长度上限：URL 等数据字段不施加错误文本的 500 字符截断，
# 仅防异常超长数据撑爆输出。
DATA_OUTPUT_LIMIT = 16 * 1024

# 纯 URL 数据字段判定：以 http(s):// 开头且不含任何空白字符的值视为 URL 本体。
_URL_DATA_PREFIX_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"\s")


def _sanitize_url_data_text(value: str, limit: int) -> str:
    """纯 URL 数据字段的轻量净化：截断后仅剥离控制字符、Bearer 令牌与 userinfo。

    查询参数是 URL 的组成部分而非凭据回显，键值裸值脱敏会把签名 URL 的查询串整体
    替换为 *** 使数据不可用；userinfo 与 Bearer 形态的凭据不受豁免影响，仍在此
    路径剥离。Bearer 短令牌替换为 *** 的膨胀与错误文本通道同口径补偿截断。
    """
    truncated = cast("str", _truncate_value_for_output(value, limit=limit))
    redacted = CONTROL_CHARS_PATTERN.sub(" ", truncated)
    redacted = _BEARER_TOKEN_PATTERN.sub(r"\1***", redacted)
    redacted = _URL_USERINFO_PATTERN.sub(r"\1", redacted)
    if len(redacted) > limit:
        redacted = _retruncate_with_single_marker(redacted, limit, len(value))
    return redacted


def sanitize_data_text(value: _SanitizedValue, limit: int = DATA_OUTPUT_LIMIT) -> _SanitizedValue:
    """对数据字段文本剥离敏感片段与控制字符，仅保留防御性大上限截断。

    url/original_url 等数据字段的取值是返回结果的一部分，500 字符级截断会使签名
    URL 不可用，故仅以 16KB 防御上限兜底。以 http(s):// 开头、strip 首尾后不含
    空白的纯 URL 走 _sanitize_url_data_text 轻量路径，不应用键值脱敏；URL 前缀但
    含空白或控制字符的混合文本与非 URL 文本仍走 sanitize_error_text 全套脱敏，
    凭据不借 URL 形态逃逸。超限 URL 的截断产物不再以 http(s):// 开头，再净化按
    错误文本通道口径收敛（产物已不可用，仅长度与形态变化）。非字符串原样返回。
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if _URL_DATA_PREFIX_PATTERN.match(stripped) and _WHITESPACE_PATTERN.search(stripped) is None:
        return cast("_SanitizedValue", _sanitize_url_data_text(stripped, limit=limit))
    return sanitize_error_text(value, limit=limit)


def is_sensitive_key(key: Any) -> bool:
    """判断键名是否命中敏感关键词。

    高确信度词子串匹配覆盖连字符、无分隔与 camelCase 变体；其余关键词按分隔符
    切分后段匹配，复合键中段同样命中，如 user.session_id 与 request-session-id，
    短词 key 不误匹配 monkey、keyboard。本函数与自由文本路径的两路径口径在此单点
    说明：带分隔符的 X_key/X_auth 复合键在前缀族内两路径一致命中；界外差异为无
    分隔复合词 usersession_id 仅自由文本路径命中、族外前缀的 hotel_key 仅本路径
    命中。
    """
    key_lower = str(key).lower()
    for substring in _SENSITIVE_KEY_SUBSTRINGS:
        if substring in key_lower:
            return True
    segments = frozenset(_KEY_SEGMENT_SPLIT_PATTERN.split(key_lower))
    return not segments.isdisjoint(_SENSITIVE_KEY_KEYWORD_SET)
