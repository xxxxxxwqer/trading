# AI Trading Arena 일일 리포트 — Day 5/10 (2026-11-11)

> **세션 정보**: Regular (09:30-16:00 ET) (실행 사이클: 13/13) | **시장 레짐(14번 기준)**: Score `3/4` | **SPY 일일/누적**: `+1.39%` / `+2.06%` | **활성 에이전트**: `13/13` (정지 `0`, 탈락 `0`)

---

## 1. 오늘의 핵심 요약 (Executive Summary)
- **일일 성과 Top / Bottom**: 최고 수익은 **12. Congress Style (House PTR Pattern)** (`+2.09%`, `+$216.15`), 최저 수익은 **8. Cautious Risk-First Trader (Gemini Flash)** (`+0.04%`, `+$4.10`)입니다.
- **벤치마크 비교**: 누적 기준 `9. Buy-and-Hold SPY` 상회 에이전트 `7/12명`, `10. Random Trader` 상회 에이전트 `6/12명`입니다.
- **전략군 흐름**: 당일 가장 강세인 전략군은 **공시 스타일 학습 (2)** (당일 평균 `+1.59%`), 가장 약세인 전략군은 **Gemini Flash LLM (4)** (당일 평균 `+0.73%`)입니다.
- **리스크·이상 징후**: 당일 신규 체결 `63건`, 리스크 관리자 거부 `0건`, 일일 정지(`DAILY_HALTED`) `0명`, 영구 탈락(`BLOWN_UP`) `0명`입니다.

---

## 2. 시장 매크로 및 기술적 레짐 브리핑

### 2.1 주요 벤치마크 및 매크로 자산 흐름
| Ticker | 종가 ($) | 일일 등락 (%) | ORB(첫 30분) 대비 위치 | SMA(20) / SMA(50) 대비 | RSI(14) | Volume Ratio | 핵심 흐름 요약 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **SPY** | $612.12 | +1.25% | ORB High 돌파 (+0.98%) | +0.79% / +1.78% | 84.1 | 1.18x | 밴드 내부 (5일 +1.90%) |
| **QQQ** | $570.16 | +2.79% | ORB High 돌파 (+2.52%) | +1.44% / +15.93% | 92.6 | 0.99x | 밴드 내부 (5일 +9.83%) |
| **TLT** | $91.93 | -0.02% | ORB 범위 내 | +0.23% / -0.93% | 55.0 | 1.50x | 밴드 내부 (5일 -0.36%) |
| **GLD** | $253.62 | +0.51% | ORB High 돌파 (+0.38%) | +0.43% / -5.29% | 79.5 | 1.52x | BB 상단 돌파 (5일 +0.90%) |

### 2.2 시장 내부 구조 (Breadth · 변동성 · 모멘텀 · 뉴스)
- **시장 폭(Breadth) 및 변동성**: 개별주 중 `SMA(50)` 상회 종목 비율 `94.3%`, `RV10/RV60` 변동성 비율 `0.55`.
- **상대강도(`RS vs SPY`) 상위 / 63일 잔차 모멘텀 상위**: RS 상위 `PLTR(+13.44), AVGO(+12.37), NVDA(+12.35), META(+11.76), AXP(+8.96)` | 잔차 모멘텀 상위 `UNH(3.50), CVX(3.46), LLY(3.33), PANW(2.88), AMZN(2.71)`.
- **과열/과매도 및 거래량 급증**: RSI(14)≥70 `AAPL(RSI 77.5), MSFT(RSI 79.2), NVDA(RSI 97.8), GOOGL(RSI 71.0), AMZN(RSI 95.0)` / RSI(14)≤30 `없음` / 거래량 비율 상위 `GOOGL(1.77x), MS(1.68x), CVX(1.62x), ISRG(1.59x), MU(1.49x)`.
- **52주 고점 근접 종목 (11·12번 기준)**: `COST(-1.9%), PG(-2.4%), KO(-3.5%), DIS(-4.4%), BRK.B(-4.5%)`.
- **당일 주요 뉴스(Alpaca Headlines)**: [AAPL,COST,WMT,AMZN,XLY] Consumer Discretionary & Club Retail Traffic Surges Into Peak Holiday Shopping Window; [LLY,ISRG,ABBV,XLV] Healthcare Innovation Leaders Gain on Strong Procedure Volume & Metabolic Pipeline Data; [JPM,GS,AXP,XLF] Capital Markets Advisory & Card Spend Trends Beat Expectations Heading Into Year-End; [PLTR,PANW,AMD,NVDA] Defense & Commercial AI Workflow Deployments Accelerate on New Federal Contract Awards; [MSFT,CRM,ADBE,ORCL] Enterprise Software CFO Survey Confirms Resilient Renewal Rates After Morning Pullback

---

## 3. 전체 에이전트 성과 리더보드 (13개 에이전트)

| 순위 | 에이전트 (전략군) | 평가액 ($) | 일일 손익 ($ / %) | 누적 수익률 (%) | vs SPY (%p) | vs Random (%p) | MDD (%) | 체결 / 청산 | 승률 (%) | PF | 평균익 / 평균손 ($) | 마찰비용 ($) | 거부 | 상태 |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1 | 4. Sector ETF Rotation (코딩 기반 (5)) | $10,814.69 | +$213.55 / +2.01% | +8.15% | +6.09%p | +5.98%p | -0.19% | 30 / 13 | 92.3% | 922.57 | +$55.35 / -$0.72 | $43.86 | 0 | `ACTIVE` |
| 2 | 3. Intraday ORB (Flat by Close) (코딩 기반 (5)) | $10,597.90 | +$161.18 / +1.54% | +5.98% | +3.92%p | +3.81%p | -0.25% | 68 / 34 | 91.2% | 36.42 | +$19.34 / -$5.49 | $95.04 | 0 | `ACTIVE` |
| 3 | 12. Congress Style (House PTR Pattern) (공시 스타일 학습 (2)) | $10,552.55 | +$216.15 / +2.09% | +5.53% | +3.47%p | +3.36%p | -0.65% | 11 / 3 | 100.0% | ∞ | +$115.30 / N/A | $15.82 | 0 | `ACTIVE` |
| 4 | 1. Trend Follower (MA / Breakout) (코딩 기반 (5)) | $10,512.38 | +$150.34 / +1.45% | +5.12% | +3.06%p | +2.95%p | -0.96% | 41 / 18 | 66.7% | 5.50 | +$41.18 / -$14.98 | $57.52 | 0 | `ACTIVE` |
| 5 | 6. News & Catalyst Trader (Gemini Flash) (Gemini Flash LLM (4)) | $10,245.79 | +$96.04 / +0.95% | +2.46% | +0.40%p | +0.29%p | -0.53% | 25 / 11 | 72.7% | 4.23 | +$31.35 / -$19.77 | $24.50 | 0 | `ACTIVE` |
| 6 | 11. Berkshire Style (13F Pattern) (공시 스타일 학습 (2)) | $10,233.91 | +$111.20 / +1.10% | +2.34% | +0.28%p | +0.17%p | -1.00% | 5 / 0 | N/A | N/A | N/A / N/A | $6.99 | 0 | `ACTIVE` |
| 7 | **10. Random Trader (Luck Control)** (SPY / 랜덤 (2)) | $10,217.39 | +$114.34 / +1.13% | +2.17% | +0.11%p | -0.00%p | -0.74% | 34 / 16 | 62.5% | 3.73 | +$20.28 / -$9.06 | $47.02 | 14 | `ACTIVE` |
| 8 | **9. Buy-and-Hold SPY (Benchmark)** (SPY / 랜덤 (2)) | $10,205.54 | +$139.87 / +1.39% | +2.06% | +0.00%p | -0.11%p | -1.06% | 1 / 0 | N/A | N/A | N/A / N/A | $5.88 | 0 | `ACTIVE` |
| 9 | 2. Mean Reversion (RSI / Bollinger) (코딩 기반 (5)) | $10,203.69 | +$7.48 / +0.07% | +2.04% | -0.02%p | -0.13%p | -1.14% | 40 / 20 | 50.0% | 2.99 | +$30.59 / -$10.22 | $53.68 | 0 | `ACTIVE` |
| 10 | 14. Claude Residual-Momentum Pullback (코딩 기반 (5)) | $10,147.52 | +$31.74 / +0.31% | +1.48% | -0.58%p | -0.69%p | -0.05% | 1 / 0 | N/A | N/A | N/A / N/A | $1.41 | 0 | `ACTIVE` |
| 11 | 7. Macro & Sector Strategist (Gemini Flash) (Gemini Flash LLM (4)) | $10,105.92 | +$90.94 / +0.91% | +1.06% | -1.00%p | -1.11%p | -1.26% | 24 / 10 | 40.0% | 0.98 | +$28.60 / -$19.54 | $28.55 | 0 | `ACTIVE` |
| 12 | 5. Technical Analyst (Gemini Flash) (Gemini Flash LLM (4)) | $10,029.01 | +$101.26 / +1.02% | +0.29% | -1.77%p | -1.88%p | -1.39% | 65 / 30 | 33.3% | 0.66 | +$11.03 / -$8.41 | $69.17 | 0 | `ACTIVE` |
| 13 | 8. Cautious Risk-First Trader (Gemini Flash) (Gemini Flash LLM (4)) | $9,992.64 | +$4.10 / +0.04% | -0.07% | -2.13%p | -2.24%p | -0.29% | 24 / 12 | 41.7% | 0.73 | +$4.06 / -$3.95 | $18.25 | 0 | `ACTIVE` |

---

## 4. 전략군별 성과 비교

| 전략군 (인원) | 당일 평균 수익률 (%) | 누적 평균 수익률 (%) | 누적 중앙값 (%) | 최고 / 최저 에이전트 | 총 체결 수 | 평균 마찰비용 ($) | 총 리스크 거부 |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: |
| 코딩 기반 (5) | +1.08% | +4.55% | +5.12% | 4. Sector ETF Rotation (+8.15%) / 14. Claude Residual-Momentum Pullback (+1.48%) | 180 | $50.30 | 0 |
| Gemini Flash LLM (4) | +0.73% | +0.94% | +0.68% | 6. News & Catalyst Trader (Gemini Flash) (+2.46%) / 8. Cautious Risk-First Trader (Gemini Flash) (-0.07%) | 138 | $35.12 | 0 |
| 공시 스타일 학습 (2) | +1.59% | +3.94% | +3.94% | 12. Congress Style (House PTR Pattern) (+5.53%) / 11. Berkshire Style (13F Pattern) (+2.34%) | 16 | $11.41 | 0 |
| SPY / 랜덤 (2) | +1.26% | +2.12% | +2.12% | 10. Random Trader (Luck Control) (+2.17%) / 9. Buy-and-Hold SPY (Benchmark) (+2.06%) | 35 | $26.45 | 14 |

- **그룹 간 격차 분석**: 당일 `공시 스타일 학습 (2)`이 평균 `+1.59%`로 선두를 기록했고, `Gemini Flash LLM (4)`은 평균 `+0.73%`를 기록했습니다. 누적 마찰비용은 체결 빈도가 높은 전략군일수록 수익률 차감 폭이 크게 나타났습니다.

---

## 5. 스페셜 분석: 14번 `claude_rmp` 텔레메트리 및 가설 검증

| 항목 | 당일 수치 / 상태 | 비고 |
| :--- | :--- | :--- |
| **레짐 점수 (`0~4`) 및 허용 슬롯** | `Score 3/4` (허용 슬롯: `3`개) | C1~C4: `{'C1': True, 'C2': False, 'C3': True, 'C4': True}` |
| **포트폴리오 리스크 (`Heat / HeatCap`)** | `0.53% / 3.50%` | `$54.28 / $354.83` |
| **거래당 평균 R (`Average R`)** | `N/A` (청산 `0`건 기준) | 누적 기준 |
| **필터·선정 지표 작동** | 당일 사유 코드: `SKIP_WINDOW, STOP_RAISED` | Top-5 리더: `UNH(3.50), CVX(3.46), LLY(3.33), PANW(2.88), AMZN(2.71)` |

- **가설 H1~H3 반증 트래커 (SPY 상승일 vs 하락일 노출도)**: 누적 `5일` 표본 기준 SPY 상승일 평균 노출도 `10.5%` vs SPY 하락일 평균 노출도 `20.2%`. 단기 표본(`Day 5/10`)이므로 통계적 확정은 10거래일 종료 후 판정합니다.

---

## 6. 당일 매매 복기, 리스크 거부 및 마찰 감사

### 6.1 주요 진입·청산 복기 (Best & Worst / 특이 체결)
| 에이전트 | 종목 | 구분 | 결정→체결 시각 (ET) | 기준가→체결가 ($) | 실현손익 ($ / %) | 신뢰도 | 근거 및 지표 정합성 평가 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 12. Congress Style (House PTR Pattern) | QCOM | `SELL` | 10:00→10:30 | $187.05→$186.92 | +$121.96 (+6.08%) | 1.00 | Take-profit triggered at $187.05 (intrabar high $187.50 >= target $187.05) |
| 12. Congress Style (House PTR Pattern) | AXP | `SELL` | 10:00→10:30 | $360.06→$359.81 | +$113.55 (+5.58%) | 1.00 | Take-profit triggered at $360.06 (intrabar high $361.10 >= target $360.06) |
| 4. Sector ETF Rotation | XLI | `SELL` | 10:30→11:00 | $164.57→$164.45 | +$72.48 (+3.45%) | 1.00 | Take-profit triggered at $164.57 (intrabar high $164.88 >= target $164.57) |
| 4. Sector ETF Rotation | XLK | `SELL` | 10:00→10:30 | $277.59→$277.40 | +$71.96 (+3.45%) | 1.00 | Take-profit triggered at $277.59 (intrabar high $279.56 >= target $277.59) |
| 4. Sector ETF Rotation | XLF | `SELL` | 10:00→10:30 | $58.55→$58.51 | +$70.25 (+3.35%) | 1.00 | Take-profit triggered at $58.55 (intrabar high $58.83 >= target $58.55) |
| 10. Random Trader (Luck Control) | MS | `SELL` | 15:30→15:35 | $140.51→$140.41 | +$60.22 (+3.00%) | 1.00 | Take-profit triggered at $140.51 (intrabar high $140.54 >= target $140.51) |
| 1. Trend Follower (MA / Breakout) | MU | `SELL` | 10:30→11:00 | $115.86→$115.78 | +$55.44 (+2.69%) | 1.00 | Take-profit triggered at $115.86 (intrabar high $115.93 >= target $115.86) |
| 3. Intraday ORB (Flat by Close) | NVDA | `SELL` | 15:30→15:35 | $186.05→$185.94 | +$51.79 (+2.46%) | 1.00 | Take-profit triggered at $186.05 (intrabar high $186.54 >= target $186.05) |
| 1. Trend Follower (MA / Breakout) | TSLA | `SELL` | 15:30→15:35 | $357.95→$357.74 | +$50.49 (+2.45%) | 1.00 | Take-profit triggered at $357.95 (intrabar high $359.59 >= target $357.95) |
| 3. Intraday ORB (Flat by Close) | AXP | `SELL` | 15:30→09:30 | $356.87→$356.62 | +$41.34 (+2.00%) | 1.00 | End-of-day mandatory flatten for AXP (5.9172 shares) |
| 3. Intraday ORB (Flat by Close) | AXP | `SELL` | 13:00→13:30 | $368.22→$367.96 | +$36.73 (+1.75%) | 1.00 | Take-profit triggered at $368.22 (intrabar high $368.64 >= target $368.22) |
| 7. Macro & Sector Strategist (Gemini Flash) | XLP | `SELL` | 14:30→15:00 | $85.07→$85.01 | +$33.58 (+1.87%) | 1.00 | Take-profit triggered at $85.07 (intrabar high $85.14 >= target $85.07) |
| 6. News & Catalyst Trader (Gemini Flash) | XLC | `SELL` | 15:30→15:35 | $99.63→$99.56 | +$32.38 (+2.13%) | 1.00 | Take-profit triggered at $99.63 (intrabar high $100.07 >= target $99.63) |
| 6. News & Catalyst Trader (Gemini Flash) | TSLA | `SELL` | 15:00→15:30 | $357.07→$356.85 | +$29.81 (+2.11%) | 0.77 | Harvesting +2.15% catalyst gain on TSLA as post-news momentum stabilizes at RSI(14)=80.8. |
| 3. Intraday ORB (Flat by Close) | MRK | `SELL` | 15:30→09:30 | $106.49→$106.42 | +$29.75 (+1.44%) | 1.00 | Take-profit triggered at $106.49 (intrabar high $106.84 >= target $106.49) |

- **주요 손실 원인 진단**: 당일 실현 손실 거래는 총 `3건` (1. Trend Follower (MA / Breakout) XLY(AGENT_SIGNAL, -$13.19), 1. Trend Follower (MA / Breakout) XLY(AGENT_SIGNAL, -$6.61), 2. Mean Reversion (RSI / Bollinger) TLT(AGENT_SIGNAL, -$1.54))으로, 손절선 터치·청산 시그널 및 호가 스프레드+5 bps 체결 마찰이 반영되었습니다.

### 6.2 리스크 관리자 거부 로그 (`rejections`) 감사
- 당일 리스크 관리자 거부 건수 없음.

### 6.3 거래 마찰 비용 (스프레드 + 5 bps 슬리피지) 영향
- **3. Intraday ORB (Flat by Close)**: 누적 마찰 비용 `$95.04` (초기 자금 `$10,000` 대비 `0.95%`, 총 `68회` 체결)
- **5. Technical Analyst (Gemini Flash)**: 누적 마찰 비용 `$69.17` (초기 자금 `$10,000` 대비 `0.69%`, 총 `65회` 체결)
- **1. Trend Follower (MA / Breakout)**: 누적 마찰 비용 `$57.52` (초기 자금 `$10,000` 대비 `0.58%`, 총 `41회` 체결)

---

## 7. 다음 거래일 관전 포인트 (모니터링 전용 · 전략 수정 금지)

### 7.1 현재 보유(오픈) 포지션 손절·익절 근접도
| 에이전트 | 종목 | 진입가 ($) | 현재가 ($) | 미실현 손익 ($ / %) | 손절가 (잔여 거리 %) | 익절가 (잔여 거리 %) | 주시 포인트 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 6. News & Catalyst Trader (Gemini Flash) | TLT | $92.24 | $91.83 | -$6.68 (-0.44%) | $91.17 (-0.72%) | $94.41 (+2.80%) | 손절선 근접 주시 |
| 5. Technical Analyst (Gemini Flash) | UNP | $248.36 | $249.42 | +$6.81 (+0.43%) | $245.44 (-1.60%) | $253.62 (+1.68%) | 비중 16.0% |
| 1. Trend Follower (MA / Breakout) | GOOGL | $191.38 | $191.25 | -$1.47 (-0.07%) | $187.87 (-1.76%) | $196.46 (+2.73%) | 비중 20.0% |
| 5. Technical Analyst (Gemini Flash) | WFC | $75.05 | $75.77 | +$15.09 (+0.95%) | $74.19 (-2.08%) | $76.67 (+1.19%) | 비중 16.0% |
| 7. Macro & Sector Strategist (Gemini Flash) | XLV | $151.11 | $153.07 | +$23.35 (+1.30%) | $149.85 (-2.10%) | $154.21 (+0.75%) | 비중 18.0% |
| 5. Technical Analyst (Gemini Flash) | HON | $235.57 | $238.04 | +$16.74 (+1.05%) | $232.99 (-2.12%) | $240.76 (+1.14%) | 비중 16.1% |
| 4. Sector ETF Rotation | XLY | $223.98 | $224.55 | +$5.50 (+0.25%) | $219.71 (-2.16%) | $231.98 (+3.31%) | 비중 20.0% |
| 1. Trend Follower (MA / Breakout) | MS | $139.81 | $140.32 | +$7.64 (+0.36%) | $137.19 (-2.23%) | $143.45 (+2.23%) | 비중 20.0% |
| 10. Random Trader (Luck Control) | GOOGL | $191.38 | $191.25 | -$1.43 (-0.07%) | $186.98 (-2.23%) | $198.25 (+3.66%) | 비중 20.0% |
| 5. Technical Analyst (Gemini Flash) | AAPL | $250.27 | $254.15 | +$24.54 (+1.55%) | $248.20 (-2.34%) | $256.48 (+0.92%) | 비중 16.0% |
| 7. Macro & Sector Strategist (Gemini Flash) | WMT | $86.24 | $87.48 | +$25.83 (+1.43%) | $85.39 (-2.39%) | $87.87 (+0.45%) | 비중 18.1% |
| 7. Macro & Sector Strategist (Gemini Flash) | XLY | $221.62 | $224.55 | +$23.84 (+1.32%) | $219.18 (-2.39%) | $226.02 (+0.65%) | 비중 18.0% |
| 5. Technical Analyst (Gemini Flash) | CRM | $349.36 | $354.55 | +$23.66 (+1.49%) | $344.83 (-2.74%) | $356.33 (+0.50%) | 비중 16.1% |
| 6. News & Catalyst Trader (Gemini Flash) | SPY | $601.34 | $614.08 | +$31.74 (+2.12%) | $596.40 (-2.88%) | $617.63 (+0.58%) | 비중 14.9% |
| 7. Macro & Sector Strategist (Gemini Flash) | COST | $989.28 | $1,008.90 | +$35.72 (+1.98%) | $979.43 (-2.92%) | $1,017.75 (+0.88%) | 비중 18.2% |

### 7.2 서킷 브레이커 여유폭 및 레짐 분기점
- **서킷 브레이커 경계 에이전트**: 현재 `-10%` 탈락(`BLOWN_UP`)까지 여유폭 3.0%p 이내 또는 `-2%` 이하 일일 손실에 근접한 에이전트 없음.
- **레짐 점수 변화 가능성**: 14번 `claude_rmp` 현재 레짐 `3/4` (허용 슬롯 `3`개). 다음 거래일 `SPY` 종가 vs `SMA(50)` (`$601.39`), 개별주 Breadth `50%` 경계(`94.3%`), `RV10/RV60` `1.25` 임계치(`0.55`) 변동 여부를 주시합니다.
- **일정 체크**: 다음 미국 거래일 — **2026-11-12 Regular Session (09:30-16:00 ET)**.
