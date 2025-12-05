#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
지정된 전략 프리셋을 CONFIG 테이블에 반영하는 스크립트.

예:
    python utilities/apply_strategy_preset.py --preset balanced_champion
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Dict

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

from shared.config import get_global_config  # noqa: E402
from shared.db.connection import ensure_engine_initialized  # noqa: E402
from shared.strategy_presets import (  # noqa: E402
    CONFIG_KEY_MAP,
    apply_preset_to_config,
    get_param_defaults as get_strategy_defaults,
    get_preset as get_strategy_preset,
    list_preset_names,
)

logger = logging.getLogger("apply_strategy_preset")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply strategy preset to CONFIG table")
    parser.add_argument("--preset", default="balanced_champion", choices=list_preset_names() or None,
                        help="전략 프리셋 이름 (기본: balanced_champion)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 변경 내역만 출력")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)

    # DB 엔진 초기화 (CONFIG 테이블에 저장하기 위해 필요)
    os.environ.setdefault("DB_TYPE", "MARIADB")
    os.environ.setdefault("SECRETS_FILE", os.path.join(PROJECT_ROOT, "secrets.json"))
    ensure_engine_initialized()

    preset_params = get_strategy_preset(args.preset)
    if not preset_params:
        raise SystemExit(f"프리셋 '{args.preset}'을 찾지 못했습니다. configs/gpt_v2_strategy_presets.json을 확인하세요.")

    defaults = get_strategy_defaults()
    config = get_global_config()

    updates: Dict[str, float] = {}
    for param_key, config_key in CONFIG_KEY_MAP.items():
        value = preset_params.get(param_key, defaults.get(param_key))
        if value is None:
            logger.warning("파라미터 '%s'에 대한 기본값이 없어 건너뜁니다.", param_key)
            continue
        updates[config_key] = value

    if args.dry_run:
        logger.info("[DRY RUN] 아래 값이 업데이트될 예정입니다:")
        for cfg_key, val in updates.items():
            logger.info("  %s = %s", cfg_key, val)
        return

    apply_preset_to_config(config, preset_params, persist_to_db=True)
    for cfg_key, val in updates.items():
        logger.info("CONFIG[%s] ← %s", cfg_key, val)

    logger.info("프리셋 '%s' 적용 완료.", args.preset)


if __name__ == "__main__":
    main()

