
import json
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from shared.db.models import LLMDecisionLedger, ShadowRadarLog, MarketFlowSnapshot

logger = logging.getLogger(__name__)

class Archivist:
    """
    [Data Strategy v6.0] The Keeper of Records.
    Responsible for robust logging of Decisions, Shadow Radar, and Market Flow.
    """
    
    def __init__(self, session_factory):
        self.session_factory = session_factory
    
    def log_decision_ledger(self, data: dict):
        """
        Logs a decision to the LLM_DECISION_LEDGER.
        """
        session = self.session_factory()
        try:
            record = LLMDecisionLedger(
                timestamp=datetime.now(timezone.utc),
                stock_code=data.get('stock_code'),
                stock_name=data.get('stock_name'),
                hunter_score=data.get('hunter_score'),
                market_regime=data.get('market_regime'),
                dominant_keywords_json=json.dumps(data.get('dominant_keywords', [])),
                debate_log=data.get('debate_log'),
                counter_position_logic=data.get('counter_position_logic'),
                thinking_called=1 if data.get('thinking_called', False) else 0,
                thinking_reason=data.get('thinking_reason'),
                cost_estimate=data.get('cost_estimate', 0.0),
                gate_result=data.get('gate_result'),
                final_decision=data.get('final_decision'), # BUY/SELL/HOLD/NO_DECISION
                final_reason=data.get('final_reason'),
                schema_v="1.0"
            )
            session.add(record)
            session.commit()
            logger.info(f"💾 [Archivist] Decision stored for {data.get('stock_code')} ({data.get('final_decision')})")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"❌ [Archivist] Failed to log decision: {e}")
        finally:
            session.close()

    def log_shadow_radar(self, data: dict):
        """
        Logs a missed opportunity or rejected item to SHADOW_RADAR_LOG.
        """
        session = self.session_factory()
        try:
            record = ShadowRadarLog(
                timestamp=datetime.now(timezone.utc),
                stock_code=data.get('stock_code'),
                stock_name=data.get('stock_name'),
                rejection_stage=data.get('rejection_stage'),
                rejection_reason=data.get('rejection_reason'),
                hunter_score_at_time=data.get('hunter_score_at_time'),
                trigger_type=data.get('trigger_type'),
                trigger_value=data.get('trigger_value'),
                schema_v="1.0"
            )
            session.add(record)
            session.commit()
            logger.info(f"📡 [Archivist] Shadow Radar Ping: {data.get('stock_code')} ({data.get('rejection_stage')})")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"❌ [Archivist] Failed to log shadow radar: {e}")
        finally:
            session.close()

    def log_market_flow_snapshot(self, data: dict):
        """
        Logs a snapshot of market flow (Foreign/Program) to MARKET_FLOW_SNAPSHOT.
        """
        session = self.session_factory()
        try:
            record = MarketFlowSnapshot(
                timestamp=datetime.now(timezone.utc),
                stock_code=data.get('stock_code'),
                price=data.get('price'),
                volume=data.get('volume'),
                foreign_net_buy=data.get('foreign_net_buy'),
                institution_net_buy=data.get('institution_net_buy'),
                program_net_buy=data.get('program_net_buy'),
                data_type=data.get('data_type', 'DAILY'),
                schema_v="1.0"
            )
            session.add(record)
            session.commit()
            # logger.debug(f"🌊 [Archivist] Market Flow snapshot for {data.get('stock_code')}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"❌ [Archivist] Failed to log market flow: {e}")
        finally:
            session.close()
