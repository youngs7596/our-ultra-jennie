#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout v1.0 전략 모듈

고급 트레이딩 전략들을 제공합니다.

전략 목록:
- PairTradingStrategy: 경쟁사 악재 기반 롱/숏 페어 트레이딩
- CompetitorBacktester: 경쟁사 수혜 전략 백테스트
"""

from .pair_trading import (
    PairTradingStrategy,
    PairSignal,
    PairPosition,
    analyze_pair_opportunity,
    get_pair_trading_summary,
)

from .competitor_backtest import (
    CompetitorBacktester,
    BacktestResult,
    DecouplingEvent,
    run_full_backtest,
    backtest_single_sector,
)

__all__ = [
    # 페어 트레이딩
    'PairTradingStrategy',
    'PairSignal',
    'PairPosition',
    'analyze_pair_opportunity',
    'get_pair_trading_summary',
    
    # 백테스트
    'CompetitorBacktester',
    'BacktestResult',
    'DecouplingEvent',
    'run_full_backtest',
    'backtest_single_sector',
]

__version__ = '1.0.0'

