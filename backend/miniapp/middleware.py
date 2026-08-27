class MiniAppFrameMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/app"):
            response["Content-Security-Policy"] = (
                "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
            )
            if "X-Frame-Options" in response:
                del response["X-Frame-Options"]
        return response
