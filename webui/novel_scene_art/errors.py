"""Stable, non-sensitive error vocabulary for the scene-art worker."""

from __future__ import annotations


class SceneArtError(RuntimeError):
    """An expected fail-closed error whose message is safe for local logs."""

    __slots__ = ("code", "definite_pre_submit")

    def __init__(
        self,
        code: str,
        message: str,
        *,
        definite_pre_submit: bool = True,
    ) -> None:
        self.code = code
        self.definite_pre_submit = definite_pre_submit
        super().__init__(message)


def fail(
    code: str,
    message: str,
    *,
    definite_pre_submit: bool = True,
) -> "NoReturn":
    raise SceneArtError(
        code,
        message,
        definite_pre_submit=definite_pre_submit,
    )


from typing import NoReturn  # noqa: E402  (keeps the public class first)
