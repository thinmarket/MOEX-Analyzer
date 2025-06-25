import json
import os
import logging
from datetime import datetime, timedelta
# from collections import Counter # Удаляем, так как теперь в AlgoDetector
from algo_detector import AlgoDetector # Импортируем новый класс

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ReportGenerator:
    """
    Генерирует текстовый трейдерский отчет по рыночной ситуации на основе JSON-файла от TickerAnalyzer.
    """
    def __init__(self, analysis_data: dict):
        if not analysis_data:
            raise ValueError("Словарь с данными анализа не может быть пустым.")
        self.data = analysis_data
        self.ticker = self.data.get('ticker', 'Unknown')
        self.stats = self.data.get('summary_stats', {})
        self.order_flow = self.data.get('order_flow_data', [])
        self.volume_profile = self.data.get('volume_profile', [])
        self.large_trades = self.data.get('large_trades', [])
        self.algo_detector = AlgoDetector(self.large_trades) # Инициализируем AlgoDetector

    def _fmt(self, value):
        """Форматирует число с пробелами между разрядами (для лотов и рублей)."""
        try:
            return f"{int(value):,}".replace(",", " ")
        except Exception:
            return str(value)

    def _format_period(self) -> str:
        """Форматирует период анализа в привычный вид."""
        start = self.data['data_period']['start']
        end = self.data['data_period']['end']
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            start_str = end_dt.strftime('%d.%m.%Y %H:%M') # Отображаем дату окончания для простоты
            end_str = end_dt.strftime('%d.%m.%Y %H:%M')
            return f"Период анализа: {start_str} — {end_str}"
        except Exception:
            return f"Период анализа: {start} - {end}"

    def _find_weakness_levels(self):
        """Находит значимые уровни слабости покупателей или продавцов (только крупные сделки, группировка по близости)."""
        weakness = []
        if not self.large_trades:
            return weakness
        # Определяем порог крупной сделки (90-й квантиль по QUANTITY)
        quantities = [t['QUANTITY'] for t in self.large_trades]
        if not quantities:
            return weakness
        threshold = sorted(quantities)[int(len(quantities)*0.9)] if len(quantities) > 1 else max(quantities)
        filtered = [t for t in self.large_trades if t['QUANTITY'] >= threshold]
        # Анализируем только крупные сделки
        for i, t in enumerate(filtered[:-1]):
            price = t['PRICE']
            direction = t['BUYSELL']
            lookahead = filtered[i+1:i+6]
            if not lookahead:
                continue
            future_prices = [x['PRICE'] for x in lookahead]
            try: # Добавляем форматирование времени здесь
                trade_time_formatted = datetime.fromisoformat(t['TRADETIME']).strftime('%d.%m.%Y %H:%M:%S')
            except ValueError:
                trade_time_formatted = t['TRADETIME']

            if direction == 'B':
                if max(future_prices) < price:
                    weakness.append({'price': price, 'label': 'Слабость покупателей', 'time': trade_time_formatted, 'qty': t['QUANTITY']})
            if direction == 'S':
                if min(future_prices) > price:
                    weakness.append({'price': price, 'label': 'Слабость продавцов', 'time': trade_time_formatted, 'qty': t['QUANTITY']})
        # Группировка по близости (0.05% от цены)
        grouped = []
        for w in sorted(weakness, key=lambda x: -x['qty']):
            if not grouped or all(abs(w['price'] - g['price'])/g['price'] > 0.0005 for g in grouped):
                grouped.append(w)
        # Оставляем топ-3 по каждой стороне
        buyers = [w for w in grouped if w['label'] == 'Слабость покупателей'][:3]
        sellers = [w for w in grouped if w['label'] == 'Слабость продавцов'][:3]
        return buyers + sellers

    def _get_trade_levels_block(self):
        """Формирует блок с торговыми уровнями и пояснениями."""
        levels = []
        levels.append("В отчёт включены только наиболее значимые уровни слабости (по крупным сделкам и яркой реакции цены):")
        poc = self.stats.get('poc')
        if poc:
            levels.append(f"POC ({poc}) — зона максимального выхода объёма, выступает как магнит для цены и отражает зону интереса крупных игроков.")
        if self.volume_profile:
            sorted_clusters = sorted(self.volume_profile, key=lambda x: x['QUANTITY'], reverse=True)[:3]
            for cl in sorted_clusters:
                levels.append(f"Кластер {cl['PRICE']} — объём {self._fmt(cl['QUANTITY'])} лотов. Эти зоны показывают, где проходил основной торговый интерес.")
        weakness = self._find_weakness_levels()
        for w in weakness:
            levels.append(f"{w['label']} на {w['price']} (объём {self._fmt(w['qty'])} лотов, сделка в {w['time']}). В этих точках агрессия одной стороны была поглощена противоположной, что может указывать на важные разворотные уровни.")
        if levels:
            levels.append("Рассмотрите эти уровни для поиска потенциальных точек входа/выхода и оценки реакции цены на них!")
        return '\n'.join(levels) if levels else 'Нет ярко выраженных торговых уровней.'

    def _find_volume_spike(self, quantile=0.995):
        """Находит выходы объёма (аномальные сделки по объёму)."""
        if not self.large_trades:
            return []
        values = [t['VALUE'] for t in self.large_trades]
        if not values:
            return []
        threshold = sorted(values)[int(len(values)*quantile)] if len(values) > 1 else max(values)
        spikes = [t for t in self.large_trades if t['VALUE'] >= threshold]
        return spikes

    def _find_culmination(self):
        """Находит кульминацию продаж/покупок (серии крупных сделок в одну сторону в конце периода)."""
        if not self.large_trades:
            return None
        last_trades = self.large_trades[-10:]
        buys = [t for t in last_trades if t['BUYSELL'] == 'B']
        sells = [t for t in last_trades if t['BUYSELL'] == 'S']
        if len(buys) >= 6 and sum(t['QUANTITY'] for t in buys) > sum(t['QUANTITY'] for t in sells):
            return 'Кульминация покупок: наблюдалась серия агрессивных маркет-ордеров на объёмах выше среднего в конце сессии, что часто предшествует развороту или коррекции вниз.'
        if len(sells) >= 6 and sum(t['QUANTITY'] for t in sells) > sum(t['QUANTITY'] for t in buys):
            return 'Кульминация продаж: наблюдалась серия агрессивных маркет-ордеров на объёмах выше среднего в конце сессии, что может сигнализировать о возможном развороте или отскоке вверх.'
        return None

    def _find_fake_break(self):
        """Пытается найти закол уровня (ложный пробой POC/VWAP)."""
        poc = self.stats.get('poc')
        vwap = self.stats.get('final_vwap')
        if not poc or not vwap or not self.large_trades:
            return None
        try:
            poc_level = float(str(poc).split('-')[0].strip())
        except Exception:
            poc_level = None
        last_price = self.large_trades[-1]['PRICE'] if self.large_trades else None
        if last_price and poc_level:
            # Если был пробой POC и возврат
            prices = [t['PRICE'] for t in self.large_trades[-20:]]
            if max(prices) > poc_level and last_price < poc_level:
                return f"Закол уровня POC ({poc}): цена пробила уровень вверх, но не удержалась и вернулась под него — классический ложный пробой и ловушка для лонгистов, указывающая на слабость покупателей."
            if min(prices) < poc_level and last_price > poc_level:
                return f"Закол уровня POC ({poc}): цена пробила уровень вниз, но не удержалась и вернулась выше — ловушка для шортистов, сигнализирующая о силе покупателей."
        return None

    def _get_top_trades_block(self, top_n=3):
        """Формирует блок топ-сделок с трейдерскими формулировками."""
        if not self.large_trades:
            return "Нет крупных сделок для анализа."
        # Уникальные сделки по времени, цене, объёму, направлению
        seen = set()
        unique_trades = []
        for t in sorted(self.large_trades, key=lambda t: t['VALUE'], reverse=True):
            key = (t['TRADETIME'], t['PRICE'], t['QUANTITY'], t['BUYSELL'])
            if key not in seen:
                seen.add(key)
                unique_trades.append(t)
            if len(unique_trades) >= top_n:
                break
        lines = ["ТОП-агрессивные сделки (выходы объёма):"]
        for t in unique_trades:
            direction = 'покупка' if t['BUYSELL'] == 'B' else 'продажа'
            # --- форматируем время ---
            try:
                dt = datetime.fromisoformat(t['TRADETIME'])
                time_str = dt.strftime('%d.%m.%Y %H:%M:%S')
            except Exception:
                time_str = t['TRADETIME']
            lines.append(f"- {time_str} — {direction.upper()} на {self._fmt(t['QUANTITY'])} лотов по {t['PRICE']} (сумма {self._fmt(t['VALUE'])} рублей)")
        return '\n'.join(lines)

    # --- Новый метод: ТОП-50 сделок дня ---
    def _get_top50_trades_block(self):
        """Формирует блок топ-50 сделок дня и краткий вывод по ним."""
        if not self.large_trades:
            return "Нет крупных сделок для анализа."
        # Сортируем по VALUE
        top_trades = sorted(self.large_trades, key=lambda t: t['VALUE'], reverse=True)[:50]
        lines = ["ТОП-50 сделок дня:"]
        total_buys = sum(1 for t in top_trades if t['BUYSELL'] == 'B')
        total_sells = sum(1 for t in top_trades if t['BUYSELL'] == 'S')
        
        for idx, t in enumerate(top_trades, 1):
            direction = 'покупка' if t['BUYSELL'] == 'B' else 'продажа'
            try:
                dt = datetime.fromisoformat(t['TRADETIME'])
                time_str = dt.strftime('%d.%m.%Y %H:%M:%S')
            except Exception:
                time_str = t['TRADETIME']
            lines.append(f"{idx:2d}. {time_str} — {direction.upper()} на {self._fmt(t['QUANTITY'])} лотов по {t['PRICE']} (сумма {self._fmt(t['VALUE'])} рублей)")
        
        if total_buys > total_sells:
            lines.append(f"\nОбщий характер ТОП-50 сделок: преобладали крупные ПОКУПКИ ({total_buys} шт.)")
        elif total_sells > total_buys:
            lines.append(f"\nОбщий характер ТОП-50 сделок: преобладали крупные ПРОДАЖИ ({total_sells} шт.)")
        else:
            lines.append("\nОбщий характер ТОП-50 сделок: сбалансированное количество покупок и продаж.")
        
        return '\n'.join(lines)

    # --- Новый метод: Признаки маркет-мейкера и алгоритмической торговли ---
    def _get_algo_and_mm_signals(self):
        """Анализирует сделки на признаки маркет-мейкера и алгоритмической торговли (использует AlgoDetector)."""
        return self.algo_detector.detect_algo_signals()

    def _get_overall_sentiment(self) -> str:
        """Определяет общее настроение рынка на основе дельты."""
        delta = self.stats.get('delta', 0)
        if delta > 0:
            return f"Рынок был под контролем быков: наблюдался устойчивый бычий импульс, кумулятивная дельта ({self._fmt(delta)}) на стороне покупателей, что указывает на преобладание агрессивных покупок и потенциальное восходящее движение."
        elif delta < 0:
            return f"Медведи доминировали: рынок находился под давлением продавцов, кумулятивная дельта ({self._fmt(delta)}) отрицательная, что говорит о преобладании агрессивных продаж и возможном дальнейшем снижении цены."
        else:
            return "Баланс сил: явного преимущества ни у одной из сторон не было. Кумулятивная дельта около нуля, указывая на равновесие в борьбе покупателей и продавцов на данном периоде."

    def _get_price_vs_vwap(self) -> str:
        """Сравнивает цену закрытия с VWAP."""
        final_vwap = self.stats.get('final_vwap')
        poc = self.stats.get('poc')
        last_price = self.large_trades[-1].get('PRICE') if self.large_trades else None
        if not last_price and self.volume_profile:
            last_price_str = self.volume_profile[-1]['PRICE']
            if '(' in last_price_str and ']' in last_price_str:
                try:
                    last_price = float(last_price_str.split(',')[0].replace('(', '').replace('[', ''))
                except ValueError:
                    logging.warning(f"Не удалось распарсить цену из интервала POC/VWAP: {last_price_str}")
                    last_price = None
            else:
                 last_price = float(last_price_str)
        if not last_price:
            return "Нет данных о цене закрытия для сравнения с VWAP."
        
        # Добавляем порог для более осмысленного вывода
        if abs(last_price - final_vwap) < (final_vwap * 0.0005): # Если разница меньше 0.05%
            return f"Цена закрытия ({self._fmt(last_price)}) находится очень близко к VWAP ({self._fmt(final_vwap)}), что указывает на нейтральное завершение периода. Это может быть как отскок от VWAP, так и его пробой."
        elif last_price > final_vwap:
            return f"Цена закрытия ({self._fmt(last_price)}) находится выше VWAP ({self._fmt(final_vwap)}) — это расценивается как бычий сигнал, поскольку сделки совершались выше средневзвешенной цены. Однако, стоит отслеживать возможную разгрузку объёмов на высоких ценовых уровнях или ложный пробой."
        else:
            return f"Цена закрытия ({self._fmt(last_price)}) находится ниже VWAP ({self._fmt(final_vwap)}) — это медвежий сигнал, указывающий на давление продавцов и возможность дальнейшего снижения или срабатывания стоп-лоссов. Возможен отскок от VWAP снизу."

    def _analyze_session_dynamics(self) -> list[str]:
        """Анализирует динамику сессии по кумулятивной дельте."""
        dynamics = []
        if not self.order_flow:
            return ["Нет данных для анализа динамики сессии."]
        
        deltas = [item['cumulative_delta'] for item in self.order_flow]
        if not deltas:
            return ["Нет данных о кумулятивной дельте для анализа динамики сессии."]
        
        # 1. Общее направление с открытия
        initial_delta = deltas[0]
        start_time = datetime.fromisoformat(self.order_flow[0]['TRADETIME']).strftime('%H:%M')
        if initial_delta < 0:
            dynamics.append(f"С открытия продавцы устроили медвежий захват, кумулятивная дельта резко ушла в минус ({self._fmt(initial_delta)}) — агрессивные продажи преобладали, задавая тон в начале сессии.")
        else:
            dynamics.append(f"С открытия быки взяли инициативу, кумулятивная дельта в плюсе ({self._fmt(initial_delta)}) — активные покупки задавали тон в начале сессии, оказывая давление на продавцов.")

        # Поиск переломных моментов
        min_delta = min(deltas)
        max_delta = max(deltas)
        min_time = datetime.fromisoformat(self.order_flow[deltas.index(min_delta)]['TRADETIME']).strftime('%H:%M')
        max_time = datetime.fromisoformat(self.order_flow[deltas.index(max_delta)]['TRADETIME']).strftime('%H:%M')

        if max_delta != min_delta: # Добавляем условие, чтобы не было одинаковых пиков
            dynamics.append(f"Максимальное давление продавцов было зафиксировано в {min_time} (кумулятивная дельта: {self._fmt(min_delta)}) — это был пик агрессивных продаж, после которого возможно изменение направления.")
            dynamics.append(f"Пик бычьей активности наблюдался в {max_time} (кумулятивная дельта: {self._fmt(max_delta)}) — это отражает максимум силы покупателей, часто предшествующий коррекции или развороту.")
        else:
            dynamics.append(f"Кумулятивная дельта не показала ярко выраженных пиков покупателей или продавцов в течение сессии, указывая на флэт или боковое движение.")


        # Анализ крупных разворотов дельты (фиксация прибыли)
        if max_delta > 0:
            peak_index = deltas.index(max_delta)
            if peak_index < len(deltas) -1:
                final_delta = deltas[-1]
                if (max_delta - final_delta) / max_delta > 0.3: # Если дельта упала более чем на 30% от пика
                    dynamics.append(f"После пика бычьей активности в {max_time} началась разгрузка: кумулятивная дельта резко снизилась, что может быть признаком фиксации прибыли крупными покупателями или сигналом к скорому развороту рынка вниз.")

        if min_delta < 0:
            trough_index = deltas.index(min_delta)
            if trough_index < len(deltas) -1:
                final_delta = deltas[-1]
                if (final_delta - min_delta) / abs(min_delta) > 0.3: # Если дельта выросла более чем на 30% от минимума
                    dynamics.append(f"После пика давления продавцов в {min_time} начался откуп: кумулятивная дельта значительно выросла, что может указывать на поглощение продаж и потенциальный отскок или разворот вверх.")

        return dynamics

    def _get_key_levels(self) -> list[str]:
        """Определяет ключевые уровни из профиля объема."""
        poc = self.stats.get('poc')
        levels = []
        if poc:
            levels.append(f"POC — зона максимального выхода объёма: {self._fmt(poc)}. Это ключевой уровень, который выступает как магнит для цены и отражает зону интереса маркет-мейкера или крупных участников.")
            levels.append(f"Ожидайте повышенную активность и борьбу вокруг этого уровня.")
        else:
            levels.append("POC не определён или отсутствует в данных.")
        return levels

    def _get_risk_and_alternative(self) -> list[str]:
        """Определяет потенциальные риски и альтернативные сценарии."""
        risks = []
        deltas = [item['cumulative_delta'] for item in self.order_flow]
        if not deltas:
            return ["Нет данных для анализа рисков и альтернативных сценариев."]
        max_delta = max(deltas)
        min_delta = min(deltas)
        final_delta = deltas[-1]
        
        # 1. Резкое падение дельты после пика (разгрузка)
        if max_delta > 0 and (max_delta - final_delta) / max_delta > 0.3:
            risks.append("Разгрузка на хаях: после мощного бычьего импульса кумулятивная дельта резко ушла вниз, что может сигнализировать о фиксации прибыли крупными игроками и возможном развороте или глубокой коррекции. Будьте осторожны с лонгами!")
        # 2. Резкий рост дельты после минимума (откуп)
        if min_delta < 0 and (final_delta - min_delta) / abs(min_delta) > 0.3:
            risks.append("Активный откуп на лоях: после сильного медвежьего давления кумулятивная дельта значительно выросла, что указывает на поглощение продаж и потенциальный отскок или разворот вверх. Это может быть ловушкой для шортистов.")
        
        # 3. Цена закрытия ниже POC или VWAP
        final_vwap = self.stats.get('final_vwap')
        poc = self.stats.get('poc')
        last_price = self.large_trades[-1].get('PRICE') if self.large_trades else None
        if not last_price and self.volume_profile:
            last_price_str = self.volume_profile[-1]['PRICE']
            if '(' in last_price_str and ']' in last_price_str:
                try:
                    last_price = float(last_price_str.split(',')[0].replace('(', '').replace('[', ''))
                except ValueError:
                    last_price = None
            else:
                 last_price = float(last_price_str)


        if final_vwap and last_price:
            if last_price < final_vwap:
                risks.append(f"Цена закрытия ({self._fmt(last_price)}) находится ниже VWAP ({self._fmt(final_vwap)}) — это усиливает медвежий сигнал, указывая на доминирование продавцов. Возможно дальнейшее снижение или тест более низких уровней.")
            elif last_price > final_vwap and abs(last_price - final_vwap) < (final_vwap * 0.0005):
                risks.append(f"Цена закрытия ({self._fmt(last_price)}) удержалась на VWAP ({self._fmt(final_vwap)}) — это может быть точкой отскока или местом набора позиции крупным игроком.")

        if poc and last_price:
            try:
                poc_level = float(str(poc).split('-')[0].strip())
                if last_price < poc_level:
                    risks.append(f"Цена закрытия ({self._fmt(last_price)}) ниже POC ({self._fmt(poc)}) — это может быть признаком ложного пробоя POC или того, что уровень перешел под контроль продавцов. Ожидайте продолжения снижения.")
                elif last_price > poc_level and abs(last_price - poc_level) < (poc_level * 0.0005):
                     risks.append(f"Цена закрытия ({self._fmt(last_price)}) удержалась на POC ({self._fmt(poc)}) — этот уровень может стать сильной поддержкой (или сопротивлением).")
            except Exception:
                pass

        # 4. Резкое падение дельты в самом конце периода
        if len(deltas) > 5 and final_delta < deltas[-5]:
            risks.append("В конце сессии кумулятивная дельта резко ушла вниз — это может указывать на агрессивный сквиз продавцов и выбивание части лонгистов. Будьте готовы к сильному импульсу против тренда.")
        
        # 5. Крупные продажи/покупки на экстремумах
        if self.large_trades:
            last_trades = self.large_trades[-10:]
            max_price_trade = max(last_trades, key=lambda x: x['PRICE'])
            min_price_trade = min(last_trades, key=lambda x: x['PRICE'])

            if max_price_trade['BUYSELL'] == 'S':
                risks.append(f"В финале сессии (на максимумах) прошла серия агрессивных продаж по {max_price_trade['PRICE']} — это может быть кульминацией бычьего движения и сменой сценария. Ищите подтверждение для шорта.")
            if min_price_trade['BUYSELL'] == 'B':
                risks.append(f"В финале сессии (на минимумах) прошла серия агрессивных покупок по {min_price_trade['PRICE']} — это может быть кульминацией медвежьего движения и сигналом к развороту. Ищите подтверждение для лонга.")
        
        fake_break = self._find_fake_break()
        if fake_break:
            risks.append(fake_break)
        
        if not risks:
            risks.append("Явных признаков манипуляций и разворота не обнаружено. Продолжайте следить за выходом объёма и реакцией цены на ключевых уровнях.")
        return risks

    def generate_full_report(self) -> str:
        """Генерирует полный текстовый отчет."""
        report_parts = [
            f"Торговый отчёт по {self.ticker}",
            self._format_period(),
            "="*40,
            "1. ОБЩИЙ РЫНОЧНЫЙ СЕНТИМЕНТ: Краткий обзор общего настроения рынка на основе кумулятивной дельты и её сравнения с VWAP.",
            f"- {self._get_overall_sentiment()}",
            f"- {self._get_price_vs_vwap()}",
            "-"*20,
            "2. СЦЕНАРИЙ СЕССИИ: Детальный разбор динамики кумулятивной дельты и поведения цены в течение торгового дня.", # Обновленный заголовок
        ]
        report_parts.extend([f"- {line}" for line in self._analyze_session_dynamics()])
        report_parts.append("-"*20)
        report_parts.append("3. КЛЮЧЕВЫЕ УРОВНИ: Определение наиболее значимых ценовых уровней на основе объёмного анализа.") # Обновленный заголовок
        report_parts.extend([f"- {line}" for line in self._get_key_levels()])
        report_parts.append("-"*20)
        report_parts.append("4. ТОРГОВЫЕ УРОВНИ И ЗОНЫ СЛАБОСТИ: Выявление зон, где произошло поглощение агрессии одной из сторон, что может сигнализировать о развороте.") # Обновленный заголовок
        report_parts.append(self._get_trade_levels_block())
        report_parts.append("-"*20)
        report_parts.append("5. ТОП-агрессивные сделки (выходы объёма): Наиболее крупные сделки, которые могут указывать на активность крупных игроков.") # Единый заголовок
        report_parts.append(self._get_top_trades_block(top_n=3)) # Выводим только топ-3 агрессивные сделки
        
        culmination = self._find_culmination()
        if culmination:
            report_parts.append(culmination)
            report_parts.append("-"*20)

        # --- Новый блок: ТОП-50 сделок дня ---
        report_parts.append("6. ТОП-50 СДЕЛОК ДНЯ: Детализация самых крупных сделок по сумме за весь анализируемый период.") # Обновленный заголовок
        report_parts.append(self._get_top50_trades_block())
        report_parts.append("-"*20)

        # --- Новый блок: Признаки маркет-мейкера и алгоритмической торговли ---
        report_parts.append("7. ПРИЗНАКИ МАРКЕТ-МЕЙКЕРА И АЛГОРИТМИЧЕСКОЙ ТОРГОВЛИ: Анализ паттернов, которые могут указывать на присутствие алгоритмов или маркет-мейкеров на рынке.") # Обновленный заголовок
        report_parts.extend([f"- {line}" for line in self._get_algo_and_mm_signals()])
        report_parts.append("-"*20)

        report_parts.extend([
            "8. РИСКИ, ЛОВУШКИ, МАНИПУЛЯЦИИ: Потенциальные угрозы и сигналы о манипуляциях, на которые стоит обратить внимание.", # Обновленный заголовок
        ])
        report_parts.extend([f"- {line}" for line in self._get_risk_and_alternative()])
        report_parts.append("-"*20)
        report_parts.extend([
            "9. ВЫВОДЫ ДЛЯ ТРЕЙДЕРА: Краткое резюме основных наблюдений и рекомендации по дальнейшим действиям.", # Обновленный заголовок
            f"- Общее настроение по кумулятивной дельте: {'Бычье' if self.stats.get('delta', 0) > 0 else 'Медвежье' if self.stats.get('delta', 0) < 0 else 'Нейтральное'}.", # Более точное определение настроения
            f"- Ключевые уровни для мониторинга: POC ({self._fmt(self.stats.get('poc'))}) и VWAP ({self._fmt(self.stats.get('final_vwap', 0))}). Следите за реакцией цены на эти уровни!",
            "- Будьте готовы к резким движениям: рынок любит устраивать сквизы, заколоты уровней и другие манипуляции на объёме. Всегда имейте в виду альтернативный сценарий!",
            "="*40
        ])
        # Почасовой мини-анализ
        hourly_stats = self.data.get('hourly_stats')
        if hourly_stats:
            report_parts.append("\n--- Почасовой мини-анализ: Детализация по активности в течение каждого часа ---") # Обновленный заголовок
            for h in hourly_stats:
                report_parts.append(f"{h['hour']}: Направление - {h['direction']}, дельта {h['delta']:+}, крупных сделок: {h['big_trades']}, объём покупок: {self._fmt(h['buy_vol'])} лотов, объём продаж: {self._fmt(h['sell_vol'])} лотов") # Улучшена читаемость
        return "\n".join(report_parts)