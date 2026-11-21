"""モデルの振り分け(設計書 A6・設計書 §3 のモデル配分)。

選ぶ順番:
  1. リクエストの `model` が、ロールの別名(llh-main など)か、設定にあるモデル名 → それ
  2. `model` が空・`auto` → 画像があれば vision、`llh.task` があればその担当ロール、無ければ main
未知のモデル名は断る(窓口が、設定に無いモデルを素通しにしない)。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config, ModelSpec


class RouteError(ValueError):
    pass


@dataclass(frozen=True)
class Route:
    role: str
    model: ModelSpec
    reason: str


def has_images(messages: list) -> bool:
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in (
                    "image_url",
                    "input_image",
                    "image",
                ):
                    return True
    return False


def resolve(cfg: Config, model: str | None, task: str | None, messages: list) -> Route:
    wanted = (model or "").strip()
    if wanted and wanted.lower() != "auto":
        for spec in cfg.models.values():
            if wanted in (spec.alias, spec.name, spec.role):
                return Route(spec.role, spec, f"model={wanted}")
        known = ", ".join(sorted({s.alias for s in cfg.models.values()} | {"auto"}))
        raise RouteError(f"未知のモデルです: {wanted}(使えるのは {known})")

    if has_images(messages):
        spec = _role_for_task(cfg, "vision") or cfg.model_for_role("main")
        return Route(spec.role, spec, "image")
    if task:
        spec = _role_for_task(cfg, task)
        if spec:
            return Route(spec.role, spec, f"task={task}")
        raise RouteError(f"未知の task です: {task}")
    main = cfg.model_for_role("main")
    return Route(main.role, main, "default")


def _role_for_task(cfg: Config, task: str) -> ModelSpec | None:
    for spec in cfg.models.values():
        if task in spec.tasks:
            return spec
    return None
