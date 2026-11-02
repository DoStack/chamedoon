class TelegramAuthError(Exception):
    def __init__(self, message: str, *, status_code: int = 401, code: str = "auth_invalid") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
