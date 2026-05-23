# -*- coding: utf-8 -*-
"""123 网盘自定义异常模块。

定义 123 网盘相关操作中可能抛出的异常类。
"""


class Cloud123Error(Exception):
    """123 网盘基础异常类。

    所有 123 网盘相关异常的父类。
    """

    def __init__(self, message: str = "123 网盘操作异常") -> None:
        """初始化异常。

        Args:
            message: 异常描述信息。
        """
        self.message = message
        super().__init__(self.message)


class AuthenticationError(Cloud123Error):
    """认证异常。

    当 Token 无效、已过期或登录失败时抛出。
    """

    def __init__(self, message: str = "认证失败") -> None:
        super().__init__(message)


class TokenExpiredError(AuthenticationError):
    """Token 过期异常。

    当访问令牌已过期需要重新登录时抛出。
    """

    def __init__(self, message: str = "Token 已过期") -> None:
        super().__init__(message)


class NetworkError(Cloud123Error):
    """网络异常。

    当网络请求失败（超时、连接错误等）时抛出。
    """

    def __init__(self, message: str = "网络请求失败") -> None:
        super().__init__(message)


class APIError(Cloud123Error):
    """API 响应异常。

    当 123pan API 返回错误状态时抛出。
    """

    def __init__(
        self,
        message: str = "API 请求失败",
        code: int | None = None,
    ) -> None:
        """初始化 API 异常。

        Args:
            message: 异常描述信息。
            code: 123pan API 返回的错误码。
        """
        self.code = code
        super().__init__(message)


class RateLimitError(Cloud123Error):
    """请求频率限制异常。

    当触发 123pan API 的频率限制时抛出。
    """

    def __init__(self, message: str = "请求过于频繁，已触发限流") -> None:
        super().__init__(message)


class FileNotFoundError(Cloud123Error):
    """文件未找到异常。

    当请求的文件或目录不存在时抛出。
    """

    def __init__(self, message: str = "文件或目录不存在") -> None:
        super().__init__(message)


class ShareError(Cloud123Error):
    """分享相关异常。

    当分享链接无效、已过期或需要提取码时抛出。
    """

    def __init__(self, message: str = "分享操作失败") -> None:
        super().__init__(message)


class LoginError(AuthenticationError):
    """登录异常。

    当用户名或密码错误，或登录被限制时抛出。
    """

    def __init__(self, message: str = "登录失败") -> None:
        super().__init__(message)
