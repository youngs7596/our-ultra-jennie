# Ultra Jennie - LLM Decision Chain (Hunter/Judge, Mermaid)

```mermaid
flowchart LR
  Quant[Quant Score\n팩터/시장] --> Hunter[Hunter_Claude]
  NewsCtx[RAG 뉴스 컨텍스트] --> Hunter
  CompBenefit[경쟁사 수혜 점수] --> Hunter
  Hunter --> Debate[Debate_Bull_vs_Bear]
  Debate --> Judge[Judge_OpenAI]
  Judge --> Decision[최종 승인/거부 + 수량]
  Decision --> Watchlist[Watchlist 업데이트]
```

