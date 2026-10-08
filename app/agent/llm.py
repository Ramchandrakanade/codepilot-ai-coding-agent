def generate_with_openrouter(
    prompt,
    system_prompt,
):
    """Generate JSON through the OpenRouter API."""

    api_key = os.getenv(
        "OPENROUTER_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured."
        )

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_object",
        },
    }

    body = json.dumps(
        payload
    ).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv(
            "OPENROUTER_SITE_URL",
            "http://localhost:5000",
        ),
        "X-Title": "CodePilot AI Coding Agent",
    }

    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    last_error = None

    for attempt in range(2):
        try:
            with urlopen(
                request,
                timeout=90,
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

                data = json.loads(raw)

            choices = data.get(
                "choices",
                [],
            )

            if not choices:
                raise RuntimeError(
                    "OpenRouter returned no choices."
                )

            message = choices[0].get(
                "message",
                {},
            )

            content = message.get(
                "content",
                "",
            )

            if isinstance(
                content,
                list,
            ):
                content = "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
                )

            if not str(content).strip():
                raise RuntimeError(
                    "OpenRouter returned empty model output."
                )

            return str(content)

        except HTTPError as exc:
            last_error = _format_http_error(
                "OpenRouter",
                exc,
            )

            if exc.code in {
                429,
                500,
                502,
                503,
                504,
            } and attempt == 0:
                time.sleep(2)
                continue

            raise RuntimeError(
                last_error
            ) from exc

        except URLError as exc:
            last_error = (
                "OpenRouter network error: "
                f"{exc.reason}"
            )

            if attempt == 0:
                time.sleep(2)
                continue

            raise RuntimeError(
                last_error
            ) from exc

        except TimeoutError:
            last_error = (
                "OpenRouter request timed out."
            )

            if attempt == 0:
                time.sleep(2)
                continue

            raise RuntimeError(
                last_error
            )

    raise RuntimeError(
        last_error
        or "OpenRouter request failed."
    )