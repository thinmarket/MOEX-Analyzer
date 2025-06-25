from collections import Counter
from datetime import datetime, timedelta
import logging

class AlgoDetector:
    def __init__(self, large_trades: list):
        self.large_trades = large_trades
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    def _fmt(self, value):
        """Форматирует число с пробелами между разрядами."""
        try:
            return f"{int(value):,}".replace(",", " ")
        except Exception:
            return str(value)

    def detect_algo_signals(self) -> list[str]:
        """
        Анализирует крупные сделки на предмет признаков алгоритмической торговли
        или активности маркет-мейкера, предоставляя более детальную информацию.
        """
        signals = []

        if not self.large_trades:
            return ["Нет данных о крупных сделках для анализа алгоритмов."]

        # 1. Серии сделок с одинаковым объёмом
        qty_counter = Counter([t['QUANTITY'] for t in self.large_trades if t['QUANTITY'] > 0])
        # Сортируем по убыванию частоты и берем топ-3
        frequent_qty_sorted = sorted([item for item in qty_counter.items() if item[1] >= 5], key=lambda x: x[1], reverse=True)[:3]
        if frequent_qty_sorted:
            qty_details = "; ".join([f"{self._fmt(q)} лотов ({count} раз)" for q, count in frequent_qty_sorted])
            signals.append(f"Обнаружены серии сделок с одинаковым объёмом: {qty_details} — это может быть признаком работы торгового алгоритма, осуществляющего единообразные входы или выходы.")

        # 2. Частые сделки (интервал < 2 сек)
        try:
            times = [datetime.fromisoformat(t['TRADETIME']) for t in self.large_trades]
            intervals = [(t2 - t1).total_seconds() for t1, t2 in zip(times, times[1:])]
            fast_trades_intervals = [dt for dt in intervals if dt < 2]
            fast_count = len(fast_trades_intervals)

            if fast_count > 10:  # Порог для определения "многих" быстрых сделок
                # Поиск наиболее интенсивного периода быстрых сделок
                max_fast_seq = 0
                current_fast_seq = 0
                start_time_seq = None
                end_time_seq = None

                for i in range(len(intervals)):
                    if intervals[i] < 2:
                        current_fast_seq += 1
                        if current_fast_seq == 1:
                            start_time_seq = times[i]
                        end_time_seq = times[i+1]
                    else:
                        if current_fast_seq > max_fast_seq:
                            max_fast_seq = current_fast_seq
                            # end_time_seq уже обновлен
                        current_fast_seq = 0
                        start_time_seq = None
                # Проверяем последовательность в конце списка
                if current_fast_seq > max_fast_seq:
                    max_fast_seq = current_fast_seq

                time_info = ""
                if max_fast_seq > 1 and start_time_seq and end_time_seq:
                    time_info = f" (наиболее интенсивная серия: {max_fast_seq} сделок за {(end_time_seq - start_time_seq).total_seconds():.1f} сек. c {start_time_seq.strftime('%H:%M:%S')} до {end_time_seq.strftime('%H:%M:%S')})"

                signals.append(f"Обнаружено {fast_count} серий быстрых сделок (интервал <2 сек){time_info} — это может указывать на высокочастотную активность или работу агрессивных алгоритмов.")
        except Exception as e:
            logging.error(f"Ошибка при анализе частых сделок: {e}")

        # 3. Кластеры по времени (>=3 сделок в течение 5 сек)
        cluster_details = []
        processed_indices = set()
        for i in range(len(self.large_trades)):
            if i in processed_indices:
                continue
            current_trade_time = datetime.fromisoformat(self.large_trades[i]['TRADETIME'])
            cluster_trades = [self.large_trades[i]]
            cluster_indices = {i}

            for j in range(i + 1, len(self.large_trades)):
                next_trade_time = datetime.fromisoformat(self.large_trades[j]['TRADETIME'])
                if (next_trade_time - current_trade_time).total_seconds() <= 5:
                    cluster_trades.append(self.large_trades[j])
                    cluster_indices.add(j)
                else:
                    break
            
            if len(cluster_trades) >= 3:
                cluster_details.append({
                    'count': len(cluster_trades),
                    'start_time': datetime.fromisoformat(cluster_trades[0]['TRADETIME']).strftime('%H:%M:%S'),
                    'end_time': datetime.fromisoformat(cluster_trades[-1]['TRADETIME']).strftime('%H:%M:%S'),
                    'total_volume': sum(t['QUANTITY'] for t in cluster_trades)
                })
                processed_indices.update(cluster_indices)
        
        if cluster_details:
            total_clusters = len(cluster_details)
            total_clustered_trades = sum(c['count'] for c in cluster_details)
            top_clusters = sorted(cluster_details, key=lambda x: x['count'], reverse=True)[:2]
            cluster_info = "; ".join([f"{c['count']} сделок ({self._fmt(c['total_volume'])} лотов) c {c['start_time']} до {c['end_time']}" for c in top_clusters])
            signals.append(f"Обнаружено {total_clusters} кластеров сделок (всего {total_clustered_trades} сделок в кластерах), где проходила интенсивная активность: {cluster_info}. Это может указывать на активность маркет-мейкера, активно поддерживающего ликвидность, или группы алгоритмов.")

        # 4. Частые сделки на одном уровне (POC)
        price_counter = Counter([t['PRICE'] for t in self.large_trades])
        frequent_price_sorted = sorted([item for item in price_counter.items() if item[1] >= 5], key=lambda x: x[1], reverse=True)[:3]
        if frequent_price_sorted:
            price_details = "; ".join([f"цена {p} ({count} раз)" for p, count in frequent_price_sorted])
            signals.append(f"Обнаружены частые сделки по одной цене: {price_details} — это может быть признаком того, что маркет-мейкер защищает или удерживает данный ценовой уровень.")

        if not signals:
            signals.append("Явных признаков алгоритмической торговли или активной работы маркет-мейкера на основе данных о крупных сделках не обнаружено. Однако, их отсутствие не исключает их присутствия на других временных интервалах или в других типах ордеров.")
        
        return signals 