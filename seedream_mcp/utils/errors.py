"""
Seedream MCP工具 - 错误处理模块

定义各种异常类型和错误处理函数。
"""

from typing import Optional, Dict, Any


class SeedreamMCPError(Exception):
    """Seedream MCP工具基础异常类"""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "error_code": self.error_code,
            "details": self.details,
        }


class SeedreamConfigError(SeedreamMCPError):
    """配置相关错误"""

    pass


class SeedreamAPIError(SeedreamMCPError):
    """API调用相关错误"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = super().to_dict()
        result.update(
            {
                "status_code": self.status_code,
                "response_data": self.response_data,
            }
        )
        return result


class SeedreamValidationError(SeedreamMCPError):
    """参数验证错误"""

    def __init__(self, message: str, field: Optional[str] = None, value: Optional[Any] = None):
        super().__init__(message)
        self.field = field
        self.value = value

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = super().to_dict()
        result.update(
            {
                "field": self.field,
                "value": self.value,
            }
        )
        return result


class SeedreamTimeoutError(SeedreamMCPError):
    """超时错误"""

    pass


class SeedreamNetworkError(SeedreamMCPError):
    """网络连接错误"""

    pass


def handle_api_error(response_status: int, response_data: Dict[str, Any]) -> SeedreamAPIError:
    """处理API错误响应

    Args:
        response_status: HTTP状态码
        response_data: 响应数据

    Returns:
        SeedreamAPIError实例
    """
    error_message = "API调用失败"

    # 根据状态码提供更具体的错误信息
    if response_status == 400:
        error_message = "请求参数错误"
    elif response_status == 401:
        error_message = "API密钥无效或已过期"
    elif response_status == 403:
        error_message = "访问被拒绝，请检查API权限"
    elif response_status == 404:
        error_message = "API端点不存在"
    elif response_status == 429:
        error_message = "请求频率超限，请稍后重试"
    elif response_status >= 500:
        error_message = "服务器内部错误"

    # 尝试从响应中提取更详细的错误信息
    if isinstance(response_data, dict):
        if "error" in response_data:
            error_detail = response_data["error"]
            if isinstance(error_detail, dict) and "message" in error_detail:
                error_message = f"{error_message}: {error_detail['message']}"
            elif isinstance(error_detail, str):
                error_message = f"{error_message}: {error_detail}"
        elif "message" in response_data:
            error_message = f"{error_message}: {response_data['message']}"

    return SeedreamAPIError(
        message=error_message, status_code=response_status, response_data=response_data
    )


def format_error_for_user(error: Exception) -> str:
    """格式化错误信息供用户查看

    Args:
        error: 异常实例

    Returns:
        格式化的错误信息字符串
    """
    if isinstance(error, SeedreamConfigError):
        return f"配置错误: {error.message}"
    elif isinstance(error, SeedreamAPIError):
        if error.status_code == 401:
            return f"认证失败: {error.message}\n请检查您的API密钥是否正确设置。"
        elif error.status_code == 429:
            return f"请求频率超限: {error.message}\n请稍后重试。"
        else:
            return f"API调用失败: {error.message}"
    elif isinstance(error, SeedreamValidationError):
        return f"参数验证失败: {error.message}"
    elif isinstance(error, SeedreamTimeoutError):
        return f"请求超时: {error.message}\n请检查网络连接或稍后重试。"
    elif isinstance(error, SeedreamNetworkError):
        return f"网络连接错误: {error.message}\n请检查网络连接。"
    elif isinstance(error, SeedreamMCPError):
        return f"操作失败: {error.message}"
    else:
        return f"未知错误: {str(error)}"
