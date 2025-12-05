#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_optimize_backtest_gpt_v2.py
--------------------------------

`backtest_gpt_v2.py` 파라미터를 다양한 조합으로 병렬 실행해
가장 현실적인 성과(고수익/저변동)를 내는 조합을 탐색하는 유틸리티.

- AMD 7800X3D (16 쓰레드) 환경을 고려해 기본적으로 논리 코어 절반만 사용
- 필요한 파라미터만 골라 샘플링하고, 조합 수가 많을 경우 랜덤 샘플로 제한
- 각 실행은 45일/60일 등 짧은 구간을 사용해 빠르게 경향을 확인
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKTEST_SCRIPT = os.path.join(PROJECT_ROOT, "utilities", "backtest_gpt_v2.py")
DEFAULT_LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("gpt_v2_optimizer")


# ---------------------------------------------------------------------------
# 튜닝 대상 파라미터 (실서비스 의사결정에 영향이 큰 항목들 위주)
# ---------------------------------------------------------------------------

PARAMETER_GRID = {
    # 매수 조건
    "rsi_buy": [25, 30, 35],
    "breakout_buffer_pct": [0.3, 0.5, 0.8],
    "bb_buffer_pct": [1.0, 1.5, 2.0],
    "llm_threshold": [65, 70, 75],

    # 포지션 / 자금
    "max_position_allocation": [10.0, 12.0, 15.0],
    "max_stock_pct": [12.0, 15.0],
    "max_sector_pct": [30.0, 35.0],
    "cash_keep_pct": [3.0, 5.0],

    # 매도 조건
    "target_profit_pct": [6.0, 8.0, 10.0],
    "base_stop_loss_pct": [4.0, 5.0, 6.0],
    "stop_loss_atr_mult": [1.6, 1.8, 2.0],
    "sell_rsi_1": [68.0, 70.0],
    "sell_rsi_2": [73.0, 75.0],
    "sell_rsi_3": [78.0, 80.0],

    # 실행 빈도
    "max_buys_per_day": [3, 4],
    "max_hold_days": [25, 30, 35],
}


# ---------------------------------------------------------------------------
# 결과 파싱 & 점수화
# ---------------------------------------------------------------------------

RESULT_JSON_PATTERN = re.compile(
    r"__BACKTEST_RESULT_JSON_START__\s*({.*})\s*__BACKTEST_RESULT_JSON_END__",
    re.DOTALL,
)


def parse_backtest_output(output: str) -> Dict[str, Optional[float]]:
    """backtest_gpt_v2.py stdout/stderr에서 지표를 추출."""
    result = {
        "success": False,
        "total_return_pct": None,
        "monthly_return_pct": None,
        "mdd_pct": None,
        "final_equity": None,
    }

    # JSON 블록이 있다면 우선 사용
    match = RESULT_JSON_PATTERN.search(output)
    if match:
        try:
            data = json.loads(match.group(1))
            result.update({
                "success": True,
                "total_return_pct": data.get("total_return_pct"),
                "monthly_return_pct": data.get("monthly_return_pct"),
                "mdd_pct": data.get("mdd_pct"),
                "final_equity": data.get("final_equity"),
            })
            return result
        except Exception:
            pass

    def _extract(pattern: str) -> Optional[float]:
        # 로그 프리픽스(날짜, 시간, 레벨 등)를 건너뛰고 매칭하도록 수정
        # 예: "2025-11-28 15:07:14,354 - INFO - [_report] - 최종 누적 수익률: 0.72%"
        m = re.search(pattern, output)
        return float(m.group(1)) if m else None

    result["total_return_pct"] = _extract(r"최종 누적 수익률:\s*([\-0-9.]+)%")
    result["monthly_return_pct"] = _extract(r"월간 수익률:\s*([\-0-9.]+)%")
    result["mdd_pct"] = _extract(r"최대 낙폭\(MDD\):\s*([0-9.]+)%")
    equity_match = re.search(r"최종 자산:\s*([0-9,]+)원", output)
    if equity_match:
        result["final_equity"] = float(equity_match.group(1).replace(",", ""))

    result["success"] = all(result[k] is not None for k in ("total_return_pct", "monthly_return_pct", "mdd_pct"))
    return result


def score_result(result: Dict[str, Optional[float]]) -> float:
    """총 수익률/월간 수익률을 높이고, MDD는 낮추는 방향으로 점수 계산."""
    if not result["success"]:
        return -1e6

    total_return = result["total_return_pct"] or 0.0
    monthly_return = result["monthly_return_pct"] or 0.0
    mdd = result["mdd_pct"] or 0.0

    # 현실적인 조합: 총 수익률 2배 가중 + 월간 8배 가중 - MDD 6배 패널티
    score = total_return * 2.0 + monthly_return * 8.0 - mdd * 6.0

    # MDD가 12%를 넘어가면 강한 페널티
    if mdd > 12.0:
        score -= (mdd - 12.0) * 20.0
    return score


# ---------------------------------------------------------------------------
# 단일 백테스트 실행 (서브 프로세스)
# ---------------------------------------------------------------------------

def run_single_backtest(params: Dict[str, float], args: argparse.Namespace) -> Dict:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    # 로그 디렉토리 생성
    log_dir = os.path.join(PROJECT_ROOT, "logs", "opt_runs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file_path = os.path.join(log_dir, f"backtest_{timestamp}_{unique_id}.log")

    cmd = [
        sys.executable,
        BACKTEST_SCRIPT,
        "--days", str(args.days),
        "--log-level", "INFO",
        "--log-dir", os.path.join("logs", "opt_runs"),
        "--universe-limit", str(args.universe_limit),
        "--top-n", str(args.top_n),
    ]

    for key, value in params.items():
        cmd.append(f"--{key.replace('_', '-')}")
        cmd.append(str(value))

    # stdout/stderr를 파일로 리다이렉션 (메모리 버퍼링 방지)
    try:
        started = time.time()
        with open(log_file_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=PROJECT_ROOT,
                timeout=args.timeout,
            )
        elapsed = time.time() - started

        # 결과 파싱을 위해 로그 파일 읽기
        # (파일이 너무 클 경우를 대비해 필요한 부분만 읽거나 예외처리 할 수도 있으나, 
        #  현재는 결과 파싱을 위해 전체를 읽어야 함. 단, subprocess pipe buffer 문제는 해결됨)
        if os.path.exists(log_file_path):
            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                output = f.read()
        else:
            output = ""

        if proc.returncode != 0:
            return {"success": False, "error": f"Process failed (code {proc.returncode})", "params": params}

        metrics = parse_backtest_output(output)
        metrics["params"] = params
        metrics["elapsed"] = elapsed
        metrics["score"] = score_result(metrics)
        return metrics

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout", "params": params}
    except Exception as exc:
        return {"success": False, "error": str(exc), "params": params}
    # 로그 파일은 디버깅을 위해 남겨둠 (삭제하지 않음)


# ---------------------------------------------------------------------------
# Optimizer 본체
# ---------------------------------------------------------------------------

@dataclass
class OptimizationResult:
    params: Dict[str, float]
    total_return_pct: float
    monthly_return_pct: float
    mdd_pct: float
    score: float
    elapsed: float = field(default=0.0)


class GPTV2Optimizer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.results: List[OptimizationResult] = []
        self.best_result: Optional[OptimizationResult] = None
        
        # 기본적으로 타임스탬프가 포함된 새 파일명 사용
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.history_file = os.path.join(PROJECT_ROOT, f"gpt_v2_opt_results_{timestamp}.json")
        
        # Resume 모드일 경우 지정된 파일 로드
        if self.args.resume:
            self.history_file = self.args.resume
            self._load_history()
        
        self.summary_dir = os.path.join(DEFAULT_LOG_DIR, "optimize_summary")
        os.makedirs(self.summary_dir, exist_ok=True)

    def _load_history(self):
        if not os.path.exists(self.history_file):
            logger.warning(f"Resume 파일이 존재하지 않습니다: {self.history_file}")
            return

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            for entry in data.get("results", []):
                # OptimizationResult 객체로 변환하여 메모리에 로드
                res = OptimizationResult(
                    params=entry["params"],
                    total_return_pct=entry["total_return_pct"],
                    monthly_return_pct=entry["monthly_return_pct"],
                    mdd_pct=entry["mdd_pct"],
                    score=entry["score"],
                    elapsed=entry.get("elapsed", 0.0)
                )
                self.results.append(res)
                
                # 최고 점수 갱신
                if self.best_result is None or res.score > self.best_result.score:
                    self.best_result = res
            
            logger.info(f"🔄 이전 결과 {len(self.results)}개 로드 완료 ({self.history_file})")
        except Exception as e:
            logger.error(f"이력 로드 실패: {e}")

    def _should_skip(self, params: Dict[str, float]) -> bool:
        # 메모리에 로드된 결과에서 중복 확인
        for res in self.results:
            if res.params == params:
                return True
        return False

    def _save_results(self):
        payload = {
            "timestamp": datetime.now().isoformat(),
            "results": [r.__dict__ for r in self.results],
            "best": self.best_result.__dict__ if self.best_result else None,
        }
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("💾 결과 저장 완료 (%s)", self.history_file)
        self._write_summary()

    def _write_summary(self):
        if not self.results:
            return
        sorted_results = sorted(self.results, key=lambda r: r.score, reverse=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_path = os.path.join(self.summary_dir, f"gpt_v2_opt_summary_{timestamp}.txt")
        latest_path = os.path.join(self.summary_dir, "gpt_v2_opt_summary_latest.txt")

        lines = []
        lines.append("=== GPT v2 Optimization Summary ===")
        lines.append(f"생성 시각: {datetime.now().isoformat()}")
        lines.append(f"총 테스트 조합: {len(self.results)}")
        if sorted_results:
            best = sorted_results[0]
            lines.append("")
            lines.append("🏅 Best Combination")
            lines.append(
                f"- Score {best.score:.2f} | Total {best.total_return_pct:.2f}% | "
                f"Monthly {best.monthly_return_pct:.2f}% | MDD {best.mdd_pct:.2f}% | "
                f"Elapsed {best.elapsed:.1f}s"
            )
            lines.append(f"- Params: {json.dumps(best.params, ensure_ascii=False)}")

        lines.append("")
        lines.append("Top 10 Results")
        for idx, entry in enumerate(sorted_results[:10], start=1):
            lines.append(
                f"{idx:02d}. Score {entry.score:.2f} | Total {entry.total_return_pct:.2f}% | "
                f"Monthly {entry.monthly_return_pct:.2f}% | MDD {entry.mdd_pct:.2f}% | "
                f"Elapsed {entry.elapsed:.1f}s | Params={json.dumps(entry.params, ensure_ascii=False)}"
            )

        text = "\n".join(lines)
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(text)
        with open(latest_path, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info("📝 요약 파일 생성: %s", summary_path)

    def generate_param_sets(self) -> List[Dict[str, float]]:
        param_names = list(PARAMETER_GRID.keys())
        param_values = list(PARAMETER_GRID.values())

        combinations = []
        for combo in itertools.product(*param_values):
            params = dict(zip(param_names, combo))
            if not self._should_skip(params):
                combinations.append(params)

        if not combinations:
            logger.info("이미 모든 조합을 테스트했습니다.")
            return []

        if len(combinations) > self.args.max_combinations:
            combinations = random.sample(combinations, self.args.max_combinations)

        random.shuffle(combinations)
        return combinations

    def run(self):
        combos = self.generate_param_sets()
        if not combos:
            return

        cpu_count = os.cpu_count() or 8
        max_workers = min(self.args.max_workers, max(1, cpu_count // 2))
        logger.info(
            "총 조합 %d개 / 병렬 worker %d개 (시스템 코어 %d)",
            len(combos),
            max_workers,
            cpu_count,
        )

        completed = 0
        total_tasks = len(combos)

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_single_backtest, params, self.args): params
                for params in combos
            }

            for future in as_completed(futures):
                completed += 1
                result = future.result()
                
                progress_str = f"({completed}/{total_tasks})"
                
                if result["success"]:
                    res_obj = OptimizationResult(
                        params=result["params"],
                        total_return_pct=result["total_return_pct"],
                        monthly_return_pct=result["monthly_return_pct"],
                        mdd_pct=result["mdd_pct"],
                        score=result["score"],
                        elapsed=result["elapsed"],
                    )
                    self.results.append(res_obj)
                    self._save_results()

                    if self.best_result is None or res_obj.score > self.best_result.score:
                        self.best_result = res_obj
                        logger.info(
                            f"{progress_str} ✅ 새로운 최고 점수! 수익률 {res_obj.total_return_pct:.2f}% / "
                            f"MDD {res_obj.mdd_pct:.2f}% / 점수 {res_obj.score:.2f}"
                        )
                    else:
                        logger.info(
                            f"{progress_str} ℹ️ 완료 (점수 {res_obj.score:.2f})"
                        )
                else:
                    logger.warning(f"{progress_str} ❌ 실패 - {result.get('error', 'Unknown')}")

        if not self.results:
            logger.info("유효한 결과를 얻지 못했습니다.")
            return

        # 최종 요약 출력
        if self.best_result:
            logger.info(
                f"🎯 최적 조합: {self.best_result.params} | "
                f"수익률 {self.best_result.total_return_pct:.2f}% / "
                f"월간 {self.best_result.monthly_return_pct:.2f}% / "
                f"MDD {self.best_result.mdd_pct:.2f}% / "
                f"점수 {self.best_result.score:.2f}"
            )
        else:
            logger.info("유효한 결과를 얻지 못했습니다.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="backtest_gpt_v2 파라미터 자동 최적화 유틸")
    parser.add_argument("--days", type=int, default=180, help="각 조합 테스트 기간 (기본 180일)")
    parser.add_argument("--universe-limit", type=int, default=50)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-combinations", type=int, default=80, help="최대 시도할 조합 수")
    parser.add_argument("--max-workers", type=int, default=os.cpu_count() // 2, help="병렬 프로세스 수")
    parser.add_argument("--resume", type=str, default=None, help="이전 결과 파일 경로 (이어하기)")
    parser.add_argument("--timeout", type=int, default=600, help="각 백테스트 타임아웃(초)")
    return parser


def main():
    args = build_parser().parse_args()
    optimizer = GPTV2Optimizer(args)
    optimizer.run()


if __name__ == "__main__":
    main()

