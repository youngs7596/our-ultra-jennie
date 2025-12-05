import { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, CandlestickData, Time } from 'lightweight-charts';
import { motion } from 'framer-motion';

interface TradingChartProps {
  stockCode: string;
  stockName: string;
  height?: number;
}

// 더미 데이터 생성 (실제로는 API에서 가져와야 함)
const generateCandlestickData = (): CandlestickData<Time>[] => {
  const data: CandlestickData<Time>[] = [];
  let basePrice = 70000;
  const startDate = new Date();
  startDate.setDate(startDate.getDate() - 90);

  for (let i = 0; i < 90; i++) {
    const date = new Date(startDate);
    date.setDate(date.getDate() + i);
    
    const volatility = Math.random() * 0.04 - 0.02;
    const open = basePrice * (1 + (Math.random() * 0.02 - 0.01));
    const close = open * (1 + volatility);
    const high = Math.max(open, close) * (1 + Math.random() * 0.01);
    const low = Math.min(open, close) * (1 - Math.random() * 0.01);
    
    data.push({
      time: date.toISOString().split('T')[0] as Time,
      open: Math.round(open),
      high: Math.round(high),
      low: Math.round(low),
      close: Math.round(close),
    });
    
    basePrice = close;
  }
  
  return data;
};

export function TradingChart({ stockCode, stockName, height = 400 }: TradingChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // 차트 생성
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0f172a' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      width: chartContainerRef.current.clientWidth,
      height: height,
      crosshair: {
        mode: 1,
        vertLine: {
          color: '#6366f1',
          width: 1,
          style: 2,
        },
        horzLine: {
          color: '#6366f1',
          width: 1,
          style: 2,
        },
      },
      rightPriceScale: {
        borderColor: '#334155',
      },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
      },
    });

    chartRef.current = chart;

    // 캔들스틱 시리즈 추가
    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderDownColor: '#ef4444',
      borderUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      wickUpColor: '#22c55e',
    });

    // 데이터 설정
    const data = generateCandlestickData();
    candlestickSeries.setData(data);

    // 볼륨 시리즈 추가
    const volumeSeries = chart.addHistogramSeries({
      color: '#6366f1',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '',
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    // 볼륨 데이터 생성
    const volumeData = data.map(d => ({
      time: d.time,
      value: Math.floor(Math.random() * 10000000) + 1000000,
      color: d.close >= d.open ? '#22c55e40' : '#ef444440',
    }));

    volumeSeries.setData(volumeData);

    // 차트 크기 조정
    chart.timeScale().fitContent();
    setIsLoading(false);

    // 리사이즈 핸들러
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [stockCode, height]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900 rounded-xl border border-slate-800 overflow-hidden"
    >
      {/* 헤더 */}
      <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-white">{stockName}</h3>
          <p className="text-sm text-slate-400">{stockCode}</p>
        </div>
        <div className="flex gap-2">
          {['1D', '1W', '1M', '3M'].map((period) => (
            <button
              key={period}
              className="px-3 py-1 text-xs font-medium rounded-md bg-slate-800 text-slate-300 hover:bg-slate-700 transition-colors"
            >
              {period}
            </button>
          ))}
        </div>
      </div>

      {/* 차트 */}
      <div className="relative">
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-900/80 z-10">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500"></div>
          </div>
        )}
        <div ref={chartContainerRef} />
      </div>

      {/* 범례 */}
      <div className="px-4 py-2 border-t border-slate-800 flex items-center gap-4 text-xs text-slate-400">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 bg-green-500 rounded-sm"></span>
          상승
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 bg-red-500 rounded-sm"></span>
          하락
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 bg-indigo-500/40 rounded-sm"></span>
          거래량
        </span>
      </div>
    </motion.div>
  );
}

export default TradingChart;

