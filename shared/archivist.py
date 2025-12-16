
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
        # [v6.0 Fix] Supports both generator context manager (session_scope) and legacy callable
        factory = self.session_factory
        # If it's a context manager (has __enter__), use with.
        # However, session_scope returns a context manager ONLY when called. 
        # But if session_factory is passed as `session_scope`, then calling it returns the CM.
        try:
            # We assume session_factory is a callable that returns a session or a context manager
            # If we were passed 'session_scope', then factory() returns the CM.
            # If we were passed 'sessionmaker', factory() returns a Session.
            
            # To be safe and compatible with the codebase's `session_scope` usage:
            # construct the context properly.
            
            # If passed session_scope, we must use it as context manager
            if hasattr(factory, '__name__') and factory.__name__ == 'session_scope':
                cm = factory()
            else:
                 # It might be a sessionmaker or just a function returning session
                 cm = factory()
            
            # If the result of factory() is a context manager (has __exit__)
            if hasattr(cm, '__exit__'):
                with cm as session:
                    self._insert_decision(session, data)
            else:
                # It's a raw session (legacy Style)
                session = cm
                try:
                    self._insert_decision(session, data)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()

        except Exception as e:
            logger.error(f"❌ [Archivist] Failed to log decision: {e}")

    def _insert_decision(self, session, data):
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
        # Note: If inside session_scope, commit happens automatically on exit. 
        # But explicit commit here doesn't hurt (it just checkpoints).
        session.commit()
        logger.info(f"💾 [Archivist] Decision stored for {data.get('stock_code')} ({data.get('final_decision')})")

    def log_shadow_radar(self, data: dict):
        """
        Logs a missed opportunity or rejected item to SHADOW_RADAR_LOG.
        """
        factory = self.session_factory
        try:
            if hasattr(factory, '__name__') and factory.__name__ == 'session_scope':
                cm = factory()
            else:
                cm = factory()
            
            if hasattr(cm, '__exit__'):
                with cm as session:
                    self._insert_shadow(session, data)
            else:
                session = cm
                try:
                    self._insert_shadow(session, data)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()
        except Exception as e:
            logger.error(f"❌ [Archivist] Failed to log shadow radar: {e}")

    def _insert_shadow(self, session, data):
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

    def log_market_flow_snapshot(self, data: dict):
        """
        Logs a snapshot of market flow (Foreign/Program) to MARKET_FLOW_SNAPSHOT.
        """
        factory = self.session_factory
        try:
            if hasattr(factory, '__name__') and factory.__name__ == 'session_scope':
                cm = factory()
            else:
                cm = factory()
            
            if hasattr(cm, '__exit__'):
                with cm as session:
                    self._insert_flow(session, data)
            else:
                session = cm
                try:
                    self._insert_flow(session, data)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()
        except Exception as e:
            logger.error(f"❌ [Archivist] Failed to log market flow: {e}")

    def _insert_flow(self, session, data):
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
