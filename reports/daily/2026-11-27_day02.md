# AI Trading Arena 일일 리포트 — Day 2/30 (2026-11-27)

> **세션 정보**: Early Close (09:30-13:00 ET) (실행 사이클: 7/13) | **시장 레짐(14번 기준)**: Score `0/4` | **SPY 일일/누적**: `+1.40%` / `+3.27%` | **활성 에이전트**: `34/34` (정지 `0`, 탈락 `0`)

---

## 1. 오늘의 핵심 요약 (Executive Summary)
- **일일 성과 Top / Bottom**: 최고 수익은 **11. Berkshire Style (13F Pattern)** (`+1.53%`, `+$153.71`), 최저 수익은 **7. Macro & Sector Strategist (Gemini Flash)** (`-0.26%`, `-$25.77`)입니다.
- **벤치마크 비교**: 누적 기준 `9. Buy-and-Hold SPY` 상회 에이전트 `0/33명`, `10. Random Trader` 상회 에이전트 `13/33명`입니다.
- **전략군 흐름**: 당일 가장 강세인 전략군은 **공시 스타일 학습 (2)** (당일 평균 `+1.43%`), 가장 약세인 전략군은 **Gemini Flash LLM (4)** (당일 평균 `+0.00%`)입니다.
- **리스크·이상 징후**: 당일 신규 체결 `42건`, 리스크 관리자 거부 `0건`, 일일 정지(`DAILY_HALTED`) `0명`, 영구 탈락(`BLOWN_UP`) `0명`입니다.

---

## 2. 시장 매크로 및 기술적 레짐 브리핑

### 2.1 주요 벤치마크 및 매크로 자산 흐름
| Ticker | 종가 ($) | 일일 등락 (%) | ORB(첫 30분) 대비 위치 | SMA(20) / SMA(50) 대비 | RSI(14) | Volume Ratio | 핵심 흐름 요약 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **SPY** | $726.88 | +1.43% | ORB Low 이탈 (-0.16%) | +1.22% / -4.10% | 67.6 | 1.41x | 밴드 내부 (5일 +4.57%) |
| **QQQ** | $646.98 | +2.06% | ORB 범위 내 | +2.62% / -8.50% | 77.9 | 0.66x | 밴드 내부 (5일 +4.23%) |
| **TLT** | $81.31 | -1.03% | ORB Low 이탈 (-0.61%) | -1.01% / +1.47% | 10.6 | 0.77x | 밴드 내부 (5일 +0.02%) |
| **GLD** | $333.26 | +1.77% | ORB Low 이탈 (-0.19%) | +2.36% / -10.18% | 83.1 | 0.84x | 밴드 내부 (5일 +2.44%) |

### 2.2 시장 내부 구조 (Breadth · 변동성 · 모멘텀 · 뉴스)
- **시장 폭(Breadth) 및 변동성**: 개별주 중 `SMA(50)` 상회 종목 비율 `49.1%`, `RV10/RV60` 변동성 비율 `1.67`.
- **상대강도(`RS vs SPY`) 상위 / 63일 잔차 모멘텀 상위**: RS 상위 `PLTR(+7.56), TSLA(+6.66), NVDA(+5.84), QCOM(+4.45), META(+4.35)` | 잔차 모멘텀 상위 `WMT(1.99), BRK.B(1.90), COST(1.69), PG(1.67), DIS(1.44)`.
- **과열/과매도 및 거래량 급증**: RSI(14)≥70 `AMZN(RSI 75.8), META(RSI 72.6), TSLA(RSI 84.1), AVGO(RSI 85.8), NFLX(RSI 88.8)` / RSI(14)≤30 `없음` / 거래량 비율 상위 `BLK(1.54x), MU(1.12x), PLTR(0.98x), XOM(0.97x), TSLA(0.94x)`.
- **52주 고점 근접 종목 (11·12번 기준)**: `COST(-1.9%), PG(-2.4%), KO(-3.5%), DIS(-4.4%), BRK.B(-4.5%)`.
- **당일 주요 뉴스(Alpaca Headlines)**: [PANW] Palo Alto Networks Collaborates with Google Cloud and Fujitsu on AI-Driven Security; [PEP] SodaStream Updates Cylinder Exchange Process; [JPM] JPMorgan Chase Discloses Change in Voting Rights in Ashtead Technology; [BLK] BlackRock Contending for Singapore Public Pension Fund Mandate; [META] Meta Cited in South African High Court Ruling Regarding Pseudonymous Content

---

## 3. 전체 에이전트 성과 리더보드 (34개 에이전트)

| 순위 | 에이전트 (전략군) | 평가액 ($) | 일일 손익 ($ / %) | 누적 수익률 (%) | vs SPY (%p) | vs Random (%p) | MDD (%) | 체결 / 청산 | 승률 (%) | PF | 평균익 / 평균손 ($) | 마찰비용 ($) | 거부 | 상태 |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | **9. Buy-and-Hold SPY (Benchmark)** (SPY / 랜덤 / 대조군 (3)) | $10,326.60 | +$142.41 / +1.40% | +3.27% | +0.00%p | +2.97%p | -0.79% | 1 / 0 | N/A | N/A | N/A / N/A | $6.86 | 0 | `ACTIVE` |
| 2 | 25. Mega-Cap Top-5 Buy & Hold (Control) (SPY / 랜덤 / 대조군 (3)) | $10,322.38 | +$101.74 / +1.00% | +3.22% | -0.05%p | +2.92%p | -1.99% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 3 | 28. Value + 63D Momentum Combo (코딩 기반 (25)) | $10,190.72 | +$96.89 / +0.96% | +1.91% | -1.36%p | +1.61%p | -0.28% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 4 | 11. Berkshire Style (13F Pattern) (공시 스타일 학습 (2)) | $10,177.73 | +$153.71 / +1.53% | +1.78% | -1.49%p | +1.48%p | -0.19% | 5 / 0 | N/A | N/A | N/A / N/A | $7.35 | 0 | `ACTIVE` |
| 5 | 2. Mean Reversion (RSI / Bollinger) (코딩 기반 (25)) | $10,172.96 | +$60.31 / +0.60% | +1.73% | -1.54%p | +1.43%p | -0.35% | 8 / 2 | 100.0% | ∞ | +$90.78 / N/A | $11.46 | 0 | `ACTIVE` |
| 6 | 12. Congress Style (House PTR Pattern) (공시 스타일 학습 (2)) | $10,157.26 | +$132.34 / +1.32% | +1.57% | -1.70%p | +1.27%p | -0.30% | 5 / 0 | N/A | N/A | N/A / N/A | $9.98 | 0 | `ACTIVE` |
| 7 | 26. Cross-Sectional 5D Short Reversal (코딩 기반 (25)) | $10,118.40 | +$118.40 / +1.18% | +1.18% | -2.09%p | +0.88%p | -0.30% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 8 | 29. 20D Up/Down Volume Accumulation (코딩 기반 (25)) | $10,099.21 | +$73.40 / +0.73% | +0.99% | -2.28%p | +0.69%p | -0.22% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 9 | 23. Minimum-Variance ETF Portfolio (코딩 기반 (25)) | $10,057.42 | +$38.96 / +0.39% | +0.57% | -2.70%p | +0.27%p | -0.15% | 5 / 0 | N/A | N/A | N/A / N/A | $5.10 | 0 | `ACTIVE` |
| 10 | 30. Regime-Adaptive Beta Switch (코딩 기반 (25)) | $10,055.68 | +$36.86 / +0.37% | +0.56% | -2.71%p | +0.26%p | -0.28% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 11 | 33. Cross-Asset Risk Appetite Ratio (코딩 기반 (25)) | $10,052.20 | +$19.03 / +0.19% | +0.52% | -2.75%p | +0.22%p | -0.19% | 4 / 0 | N/A | N/A | N/A / N/A | $4.20 | 0 | `ACTIVE` |
| 12 | 4. Sector ETF Rotation (코딩 기반 (25)) | $10,044.55 | +$50.06 / +0.50% | +0.45% | -2.82%p | +0.15%p | -0.12% | 4 / 0 | N/A | N/A | N/A / N/A | $5.60 | 0 | `ACTIVE` |
| 13 | 22. Risk Parity 3-Asset (8% Vol) (코딩 기반 (25)) | $10,032.56 | +$6.78 / +0.07% | +0.33% | -2.94%p | +0.03%p | -0.18% | 3 / 0 | N/A | N/A | N/A / N/A | $2.52 | 0 | `ACTIVE` |
| 14 | **10. Random Trader (Luck Control)** (SPY / 랜덤 / 대조군 (3)) | $10,030.03 | +$30.03 / +0.30% | +0.30% | -2.97%p | +0.00%p | -0.15% | 6 / 1 | 100.0% | ∞ | +$8.33 / N/A | $8.41 | 0 | `ACTIVE` |
| 15 | 6. News & Catalyst Trader (Gemini Flash) (Gemini Flash LLM (4)) | $10,027.14 | +$27.14 / +0.27% | +0.27% | -3.00%p | -0.03%p | -0.30% | 1 / 0 | N/A | N/A | N/A / N/A | $1.05 | 0 | `ACTIVE` |
| 16 | 1. Trend Follower (MA / Breakout) (코딩 기반 (25)) | $10,021.94 | +$21.94 / +0.22% | +0.22% | -3.05%p | -0.08%p | -0.14% | 2 / 0 | N/A | N/A | N/A / N/A | $2.80 | 0 | `ACTIVE` |
| 17 | 27. 70D High Anchoring (George-Hwang) (코딩 기반 (25)) | $10,014.50 | +$18.08 / +0.18% | +0.14% | -3.13%p | -0.16%p | -0.29% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 18 | 24. Low-Volatility Stock Anomaly (코딩 기반 (25)) | $10,008.70 | +$26.46 / +0.27% | +0.09% | -3.18%p | -0.21%p | -0.36% | 5 / 0 | N/A | N/A | N/A / N/A | $6.65 | 0 | `ACTIVE` |
| 19 | 3. Intraday ORB (Flat by Close) (코딩 기반 (25)) | $10,004.44 | +$4.44 / +0.04% | +0.04% | -3.23%p | -0.26%p | -0.05% | 2 / 1 | 100.0% | ∞ | +$4.44 / N/A | $2.81 | 0 | `ACTIVE` |
| 20 | 5. Technical Analyst (Gemini Flash) (Gemini Flash LLM (4)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 21 | 8. Cautious Risk-First Trader (Gemini Flash) (Gemini Flash LLM (4)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 22 | 14. Claude Residual-Momentum Pullback (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 23 | 17. EOD Loser Overnight Reversal (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 24 | 19. Turn-of-Month Calendar (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 25 | 20. Intraday Gap-Down Fill (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 26 | 21. Dual Momentum Macro ETF (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 27 | 31. Market Breadth Equity Timing (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 28 | 32. Panic-Dip Index Buyer (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 29 | 34. Intraday VWAP -2σ Reversion (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 30 | 35. Volatility Squeeze Breakout (코딩 기반 (25)) | $10,000.00 | +$0.00 / +0.00% | +0.00% | -3.27%p | -0.30%p | 0.00% | 0 / 0 | N/A | N/A | N/A / N/A | $0.00 | 0 | `ACTIVE` |
| 31 | 15. Kiro Pair-Spread Reversion (PSR) (코딩 기반 (25)) | $9,998.81 | -$1.19 / -0.01% | -0.01% | -3.28%p | -0.31%p | -0.03% | 1 / 0 | N/A | N/A | N/A / N/A | $0.78 | 0 | `ACTIVE` |
| 32 | 16. Overnight Drift (Close→Open) (코딩 기반 (25)) | $9,997.26 | -$2.74 / -0.03% | -0.03% | -3.30%p | -0.33%p | -0.03% | 2 / 0 | N/A | N/A | N/A / N/A | $2.73 | 0 | `ACTIVE` |
| 33 | 18. Intraday Momentum (Gao 2018) (코딩 기반 (25)) | $9,994.83 | -$5.17 / -0.05% | -0.05% | -3.32%p | -0.35%p | -0.07% | 4 / 2 | 0.0% | 0.00 | N/A / -$2.58 | $5.46 | 0 | `ACTIVE` |
| 34 | 7. Macro & Sector Strategist (Gemini Flash) (Gemini Flash LLM (4)) | $9,974.23 | -$25.77 / -0.26% | -0.26% | -3.53%p | -0.56%p | -0.59% | 1 / 0 | N/A | N/A | N/A / N/A | $1.05 | 0 | `ACTIVE` |

---

## 4. 전략군별 성과 비교

| 전략군 (인원) | 당일 평균 수익률 (%) | 누적 평균 수익률 (%) | 누적 중앙값 (%) | 최고 / 최저 에이전트 | 총 체결 수 | 평균 마찰비용 ($) | 총 리스크 거부 |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: |
| 코딩 기반 (25) | +0.22% | +0.35% | +0.04% | 28. Value + 63D Momentum Combo (+1.91%) / 18. Intraday Momentum (Gao 2018) (-0.05%) | 65 | $3.33 | 0 |
| Gemini Flash LLM (4) | +0.00% | +0.00% | +0.00% | 6. News & Catalyst Trader (Gemini Flash) (+0.27%) / 7. Macro & Sector Strategist (Gemini Flash) (-0.26%) | 2 | $0.53 | 0 |
| 공시 스타일 학습 (2) | +1.43% | +1.68% | +1.68% | 11. Berkshire Style (13F Pattern) (+1.78%) / 12. Congress Style (House PTR Pattern) (+1.57%) | 10 | $8.66 | 0 |
| SPY / 랜덤 / 대조군 (3) | +0.90% | +2.26% | +3.22% | 9. Buy-and-Hold SPY (Benchmark) (+3.27%) / 10. Random Trader (Luck Control) (+0.30%) | 12 | $7.31 | 0 |

- **그룹 간 격차 분석**: 당일 `공시 스타일 학습 (2)`이 평균 `+1.43%`로 선두를 기록했고, `Gemini Flash LLM (4)`은 평균 `+0.00%`를 기록했습니다. 누적 마찰비용은 체결 빈도가 높은 전략군일수록 수익률 차감 폭이 크게 나타났습니다.

---

## 5. 스페셜 분석: 14번 `claude_rmp` 텔레메트리 및 가설 검증

| 항목 | 당일 수치 / 상태 | 비고 |
| :--- | :--- | :--- |
| **레짐 점수 (`0~4`) 및 허용 슬롯** | `Score 0/4` (허용 슬롯: `0`개) | C1~C4: `{'C1': False, 'C2': False, 'C3': False, 'C4': False}` |
| **포트폴리오 리스크 (`Heat / HeatCap`)** | `0.00% / 3.50%` | `$0.00 / $350.00` |
| **거래당 평균 R (`Average R`)** | `N/A` (청산 `0`건 기준) | 누적 기준 |
| **필터·선정 지표 작동** | 당일 사유 코드: `SKIP_REGIME_OFF` | Top-5 리더: `WMT(1.99), BRK.B(1.90), COST(1.69), PG(1.67), DIS(1.44)` |

- **가설 H1~H3 반증 트래커 (SPY 상승일 vs 하락일 노출도)**: 누적 `2일` 표본 기준 SPY 상승일 평균 노출도 `0.0%` vs SPY 하락일 평균 노출도 `0.0%`. 단기 표본(`Day 2/30`)이므로 통계적 확정은 30거래일 종료 후 판정합니다.

---

## 6. 당일 매매 복기, 리스크 거부 및 마찰 감사

### 6.1 주요 진입·청산 복기 (Best & Worst / 특이 체결)
| 에이전트 | 종목 | 구분 | 결정→체결 시각 (ET) | 기준가→체결가 ($) | 실현손익 ($ / %) | 신뢰도 | 근거 및 지표 정합성 평가 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 2. Mean Reversion (RSI / Bollinger) | AVGO | `SELL` | 09:30→09:30 | $310.98→$310.76 | +$181.15 (+9.05%) | 1.00 | Take-profit triggered at $310.98 (intrabar high $311.04 >= target $294.75) |
| 10. Random Trader (Luck Control) | XLI | `SELL` | 12:28→12:28 | $165.31→$165.20 | +$8.33 (+0.42%) | 0.50 | Null-hypothesis random control: deterministic cycle seed (0032f23e) rolled 0.212 (< 0.25) to randomly exit held position XLI. |
| 3. Intraday ORB (Flat by Close) | AMZN | `SELL` | 12:28→12:28 | $254.80→$254.62 | +$4.44 (+0.22%) | 1.00 | End-of-day mandatory flatten for AMZN (7.8799 shares) |
| 18. Intraday Momentum (Gao 2018) | SPY | `SELL` | 12:28→12:28 | $726.88→$726.38 | -$2.71 (-0.14%) | 1.00 | End-of-day mandatory flatten for SPY (2.6826 shares) |
| 18. Intraday Momentum (Gao 2018) | QQQ | `SELL` | 12:28→12:28 | $646.98→$646.53 | -$2.46 (-0.13%) | 1.00 | End-of-day mandatory flatten for QQQ (3.0144 shares) |
| 2. Mean Reversion (RSI / Bollinger) | PEP | `SELL` | 12:02→12:02 | $144.37→$144.27 | +$0.40 (+0.02%) | 0.84 | Taking profit on PEP mean-reversion bounce: price ($144.37) reached the 20-period Bollinger midline ($144.36). |
| 1. Trend Follower (MA / Breakout) | CVX | `BUY` | 09:30→09:30 | $210.99→$211.14 | - | 0.92 | Buying CVX on momentum breakout: price $210.99 is at/near 20-bar high ($211.91) with 10-SMA ($210.91) > 30-SMA ($209.77), RSI(14) at 62.7, a |
| 1. Trend Follower (MA / Breakout) | XLU | `BUY` | 12:28→12:28 | $43.63→$43.67 | - | 0.92 | Buying XLU on momentum breakout: price $43.63 is at/near 20-bar high ($43.78) with 10-SMA ($43.64) > 30-SMA ($43.55), RSI(14) at 63.4, and v |
| 2. Mean Reversion (RSI / Bollinger) | ABBV | `BUY` | 09:30→09:30 | $294.49→$294.70 | - | 0.90 | Buying oversold dip in ABBV: RSI(14) is 15.5 with price $294.49 near lower Bollinger Band ($293.23) while holding structural support above 9 |
| 2. Mean Reversion (RSI / Bollinger) | TLT | `BUY` | 10:01→10:02 | $81.73→$81.79 | - | 0.90 | Buying oversold dip in TLT: RSI(14) is 12.6 with price $81.73 near lower Bollinger Band ($81.76) while holding structural support above 94%  |
| 2. Mean Reversion (RSI / Bollinger) | KO | `BUY` | 10:33→10:33 | $97.25→$97.32 | - | 0.72 | Buying oversold dip in KO: RSI(14) is 34.4 with price $97.25 near lower Bollinger Band ($96.83) while holding structural support above 94% o |
| 2. Mean Reversion (RSI / Bollinger) | PEP | `BUY` | 11:04→11:04 | $144.14→$144.24 | - | 0.76 | Buying oversold dip in PEP: RSI(14) is 32.8 with price $144.14 near lower Bollinger Band ($142.65) while holding structural support above 94 |
| 2. Mean Reversion (RSI / Bollinger) | MCD | `BUY` | 11:30→11:31 | $281.88→$282.07 | - | 0.84 | Buying oversold dip in MCD: RSI(14) is 28.4 with price $281.88 near lower Bollinger Band ($279.56) while holding structural support above 94 |
| 3. Intraday ORB (Flat by Close) | AMZN | `BUY` | 11:30→11:31 | $253.88→$254.06 | - | 0.75 | Buying AMZN on 30-min Opening Range Breakout: price $253.81 cleared ORH $251.17 (ORL $250.88) on 1.14x relative volume. Stop placed at openi |
| 4. Sector ETF Rotation | XLB | `BUY` | 09:30→09:30 | $51.20→$51.24 | - | 0.73 | Rotating into top-ranked sector ETF XLB (rank #3 of 11, composite RS score +0.36%, RS vs SPY -1.00%, 5d change +2.40%) trading at $51.20 abo |

- **주요 손실 원인 진단**: 당일 실현 손실 거래는 총 `2건` (18. Intraday Momentum (Gao 2018) QQQ(EOD_FLAT, -$2.46), 18. Intraday Momentum (Gao 2018) SPY(EOD_FLAT, -$2.71))으로, 손절선 터치·청산 시그널 및 호가 스프레드+5 bps 체결 마찰이 반영되었습니다.

### 6.2 리스크 관리자 거부 로그 (`rejections`) 감사
- 당일 리스크 관리자 거부 건수 없음.

### 6.3 거래 마찰 비용 (스프레드 + 5 bps 슬리피지) 영향
- **2. Mean Reversion (RSI / Bollinger)**: 누적 마찰 비용 `$11.46` (초기 자금 `$10,000` 대비 `0.11%`, 총 `8회` 체결)
- **12. Congress Style (House PTR Pattern)**: 누적 마찰 비용 `$9.98` (초기 자금 `$10,000` 대비 `0.10%`, 총 `5회` 체결)
- **10. Random Trader (Luck Control)**: 누적 마찰 비용 `$8.41` (초기 자금 `$10,000` 대비 `0.08%`, 총 `6회` 체결)

---

## 7. 다음 거래일 관전 포인트 (모니터링 전용 · 전략 수정 금지)

### 7.1 현재 보유(오픈) 포지션 손절·익절 근접도
| 에이전트 | 종목 | 진입가 ($) | 현재가 ($) | 미실현 손익 ($ / %) | 손절가 (잔여 거리 %) | 익절가 (잔여 거리 %) | 주시 포인트 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 2. Mean Reversion (RSI / Bollinger) | TLT | $81.79 | $81.31 | -$11.86 (-0.58%) | $80.26 (-1.29%) | $83.77 (+3.03%) | 손절선 근접 주시 |
| 2. Mean Reversion (RSI / Bollinger) | ABBV | $294.70 | $293.12 | -$10.82 (-0.53%) | $289.19 (-1.34%) | $301.85 (+2.98%) | 손절선 근접 주시 |
| 1. Trend Follower (MA / Breakout) | XLU | $43.67 | $43.63 | -$1.40 (-0.07%) | $42.98 (-1.50%) | $44.94 (+2.99%) | 손절선 근접 주시 |
| 4. Sector ETF Rotation | XLC | $107.22 | $107.27 | +$0.93 (+0.05%) | $105.53 (-1.62%) | $111.43 (+3.88%) | 비중 20.0% |
| 4. Sector ETF Rotation | XLP | $92.53 | $92.66 | +$2.60 (+0.13%) | $91.08 (-1.70%) | $96.17 (+3.79%) | 비중 20.0% |
| 10. Random Trader (Luck Control) | DIA | $506.82 | $506.96 | +$0.53 (+0.03%) | $497.91 (-1.79%) | $523.02 (+3.17%) | 비중 20.0% |
| 4. Sector ETF Rotation | XLF | $57.02 | $57.26 | +$8.26 (+0.41%) | $56.13 (-1.96%) | $59.26 (+3.50%) | 비중 20.0% |
| 10. Random Trader (Luck Control) | QQQ | $647.34 | $646.98 | -$1.11 (-0.06%) | $633.89 (-2.02%) | $672.89 (+4.00%) | 비중 19.8% |
| 2. Mean Reversion (RSI / Bollinger) | KO | $97.32 | $97.58 | +$5.47 (+0.27%) | $95.50 (-2.14%) | $99.69 (+2.16%) | 비중 20.0% |
| 2. Mean Reversion (RSI / Bollinger) | MCD | $282.07 | $283.27 | +$8.62 (+0.42%) | $276.80 (-2.28%) | $288.92 (+1.99%) | 비중 20.0% |
| 10. Random Trader (Luck Control) | AMZN | $254.55 | $254.80 | +$1.98 (+0.10%) | $248.66 (-2.41%) | $265.79 (+4.31%) | 비중 20.0% |
| 12. Congress Style (House PTR Pattern) | TMO | $679.73 | $683.00 | +$9.74 (+0.48%) | $665.67 (-2.54%) | $723.41 (+5.92%) | 비중 20.0% |
| 11. Berkshire Style (13F Pattern) | PEP | $144.30 | $144.43 | +$1.81 (+0.09%) | $140.59 (-2.66%) | $155.74 (+7.83%) | 비중 19.9% |
| 1. Trend Follower (MA / Breakout) | CVX | $211.14 | $213.60 | +$23.34 (+1.17%) | $207.83 (-2.70%) | $217.32 (+1.74%) | 비중 20.2% |
| 11. Berkshire Style (13F Pattern) | PG | $172.95 | $173.05 | +$1.16 (+0.06%) | $168.33 (-2.73%) | $186.46 (+7.75%) | 비중 19.9% |

### 7.2 서킷 브레이커 여유폭 및 레짐 분기점
- **서킷 브레이커 경계 에이전트**: 현재 `-10%` 탈락(`BLOWN_UP`)까지 여유폭 3.0%p 이내 또는 `-2%` 이하 일일 손실에 근접한 에이전트 없음.
- **레짐 점수 변화 가능성**: 14번 `claude_rmp` 현재 레짐 `0/4` (허용 슬롯 `0`개). 다음 거래일 `SPY` 종가 vs `SMA(50)` (`$757.93`), 개별주 Breadth `50%` 경계(`49.1%`), `RV10/RV60` `1.25` 임계치(`1.67`) 변동 여부를 주시합니다.
- **일정 체크**: 다음 미국 거래일 — **2026-11-30 Regular Session (09:30-16:00 ET)**.
