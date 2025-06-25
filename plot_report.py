import json
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import sys
import os
import pandas as pd

# --- Настройки ---
plt.style.use('seaborn-v0_8-darkgrid')

# --- Функция для форматирования чисел с пробелами ---
def fmt_num(val):
    try:
        return f"{int(val):,}".replace(",", " ")
    except Exception:
        return str(val)

# --- Загрузка данных ---
def load_analysis(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# --- Основная функция построения графика ---
def plot_report(json_path):
    data = load_analysis(json_path)
    ticker = data.get('ticker', 'TICKER')
    large_trades = data.get('large_trades', [])
    # Удаляем дубликаты сделок
    if large_trades:
        df_trades = pd.DataFrame(large_trades)
        if 'TRADENO' in df_trades.columns:
            df_trades = df_trades.drop_duplicates(subset=['TRADENO'])
        else:
            df_trades = df_trades.drop_duplicates(subset=['TRADETIME', 'PRICE', 'QUANTITY', 'BUYSELL'])
        large_trades = df_trades.to_dict('records')
    stats = data.get('summary_stats', {})
    vwap = stats.get('final_vwap')
    poc = stats.get('poc')

    # --- Подготовка данных ---
    times = [datetime.fromisoformat(t['TRADETIME']) for t in large_trades]
    prices = [t['PRICE'] for t in large_trades]
    volumes = [t['QUANTITY'] for t in large_trades]
    values = [t['VALUE'] for t in large_trades]

    # Определяем топ-10 самых крупных сделок по сумме
    top_n = 10
    top_indices = sorted(range(len(large_trades)), key=lambda i: large_trades[i]['VALUE'], reverse=True)[:top_n]
    buys = [i for i in top_indices if large_trades[i]['BUYSELL'] == 'B']
    sells = [i for i in top_indices if large_trades[i]['BUYSELL'] == 'S']

    # --- Готовим отображение номеров сделок по времени ---
    # Сортируем top_indices по времени сделки
    top_indices_sorted_by_time = sorted(top_indices, key=lambda i: large_trades[i]['TRADETIME'])
    index_to_time_rank = {i: rank+1 for rank, i in enumerate(top_indices_sorted_by_time)}

    # --- График ---
    fig, ax1 = plt.subplots(figsize=(14, 7))
    ax2 = ax1.twinx()

    # Цена (по крупным сделкам)
    ax1.plot(times, prices, label='Цена (крупные сделки)', color='tab:blue', linewidth=2)

    # Объёмы (столбики)
    ax2.bar(times, volumes, width=0.0005, alpha=0.3, color='tab:gray', label='Объём (лоты)')

    # Маркеры только для топ-10 крупных покупок/продаж
    ax1.scatter([times[i] for i in buys], [prices[i] for i in buys], s=80, color='green', marker='^', label='Крупные покупки (топ-10)')
    ax1.scatter([times[i] for i in sells], [prices[i] for i in sells], s=80, color='red', marker='v', label='Крупные продажи (топ-10)')

    # Подписи ко всем стрелкам (топ-10)
    for idx, i in enumerate(top_indices):
        t = large_trades[i]
        t_time = datetime.fromisoformat(t['TRADETIME'])
        # Увеличенное смещение: по диагонали, чередуем направления
        y_offset = 60 if idx % 2 == 0 else -60
        x_offset = -80 if idx % 3 == 0 else (80 if idx % 3 == 1 else 0)
        color = 'green' if t['BUYSELL'] == 'B' else 'red'
        deal_num = index_to_time_rank[i]
        ax1.annotate(f"#{deal_num} {t['TRADETIME'][11:16]} по {t['PRICE']}\n{fmt_num(t['QUANTITY'])} лотов\n{fmt_num(t['VALUE'])} руб.",
                     (t_time, t['PRICE']),
                     textcoords="offset points", xytext=(x_offset, y_offset), ha='center', fontsize=6,
                     color=color,
                     bbox=dict(boxstyle="round,pad=0.2", fc="#ffffe0", alpha=0.18),
                     arrowprops=dict(arrowstyle="-", color="gray", alpha=0.5, lw=1))

    # Линия VWAP
    if vwap:
        ax1.axhline(vwap, color='orange', linestyle='--', linewidth=1.5, label=f'VWAP {vwap}')
    # Линия POC
    if poc:
        try:
            poc_level = float(str(poc).split('-')[0].strip())
            ax1.axhline(poc_level, color='purple', linestyle=':', linewidth=1.5, label=f'POC {poc}')
        except Exception:
            pass

    # Оформление
    ax1.set_title(f"{ticker}: Крупные сделки, объёмы, VWAP, POC", fontsize=16)
    ax1.set_xlabel('Время')
    ax1.set_ylabel('Цена')
    ax2.set_ylabel('Объём (лоты)')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    fig.autofmt_xdate()
    ax1.legend(loc='upper left')
    ax2.legend(loc='upper right')
    plt.tight_layout()

    # Сохранение
    out_path = f"plot_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M')}.png"
    plt.savefig(out_path, dpi=150)
    print(f"График сохранён: {out_path}")
    # Открываем файл в стандартной программе просмотра изображений Windows
    try:
        os.startfile(out_path)
    except Exception as e:
        print(f"Не удалось открыть изображение автоматически: {e}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Укажите путь к JSON-файлу с анализом, например: python plot_report.py analysis_SBER_20250623_185532.json")
        sys.exit(1)
    plot_report(sys.argv[1]) 