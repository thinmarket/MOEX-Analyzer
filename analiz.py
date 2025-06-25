import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class TickerAnalyzer:
    """
    Класс для проведения анализа потока ордеров и объемного анализа 
    по данным о сделках для одного тикера.
    """
    def __init__(self, trades_df: pd.DataFrame):
        if not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
            raise ValueError("DataFrame пуст или имеет неверный формат.")
        self.df = self._preprocess_data(trades_df)
        self.ticker = self.df['SECID'].iloc[0] if not self.df.empty else 'Unknown'
        logging.info(f"Инициализирован анализатор для {self.ticker} с {len(self.df)} сделками.")

    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Подготовка данных: конвертация типов, создание нужных столбцов."""
        df = df.copy()
        df['TRADETIME'] = pd.to_datetime(df['TRADEDATE'] + ' ' + df['TRADETIME'], errors='coerce')
        df.dropna(subset=['TRADETIME'], inplace=True)
        
        for col in ['PRICE', 'QUANTITY']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Для фьючерсов и некоторых акций нет поля VALUE, создаем его
        # Это не всегда реальный объем в рублях, но позволяет сравнивать сделки
        if 'VALUE' not in df.columns:
            df['VALUE'] = df['PRICE'] * df['QUANTITY']
        else:
            df['VALUE'] = pd.to_numeric(df['VALUE'], errors='coerce')

        # Создаем "подписанный объем": положительный для покупок, отрицательный для продаж
        try:
            df['signed_volume'] = df['QUANTITY'] * np.where(df['BUYSELL'] == 'B', 1, -1)
        except Exception as e:
            logging.warning(f"Не удалось рассчитать 'signed_volume': {e}")
            df['signed_volume'] = 0

        df = df.sort_values('TRADETIME').reset_index(drop=True)
        return df.dropna(subset=['PRICE', 'QUANTITY', 'VALUE'])

    def get_order_flow_metrics(self, resample_period: str = '1Min') -> pd.DataFrame:
        """Расчет дельты и кумулятивной дельты."""
        if self.df.empty:
            return pd.DataFrame(columns=['delta', 'cumulative_delta'])
            
        logging.info(f"Расчет метрик потока ордеров с периодом {resample_period}...")
        delta_df = self.df.set_index('TRADETIME')['signed_volume'].resample(resample_period).sum().to_frame(name='delta')
        delta_df['cumulative_delta'] = delta_df['delta'].cumsum()
        return delta_df

    def get_vwap(self) -> pd.DataFrame:
        """Расчет VWAP (Volume-Weighted Average Price)."""
        if self.df.empty:
            return pd.DataFrame(columns=['TRADETIME', 'PRICE', 'vwap'])

        logging.info("Расчет VWAP...")
        q = self.df['QUANTITY'].values
        p = self.df['PRICE'].values
        
        # Для предотвращения деления на ноль, если объем равен 0 в начале
        cumulative_q = q.cumsum()
        vwap = np.divide((p * q).cumsum(), cumulative_q, out=np.zeros_like(cumulative_q, dtype=float), where=cumulative_q!=0)

        vwap_df = self.df[['TRADETIME', 'PRICE']].copy()
        vwap_df['vwap'] = vwap
        return vwap_df

    def get_volume_profile(self, bins: int = 50) -> dict:
        """Расчет профиля объема и Point of Control (POC)."""
        if self.df.empty:
            return {'volume_profile': [], 'poc_level': None, 'poc_volume': 0}

        logging.info(f"Расчет профиля объема с {bins} уровнями...")
        price_bins = pd.cut(self.df['PRICE'], bins=bins)
        volume_profile = self.df.groupby(price_bins)['QUANTITY'].sum().sort_index()
        
        if volume_profile.empty:
            return {'volume_profile': [], 'poc_level': None, 'poc_volume': 0}
            
        poc_interval = volume_profile.idxmax()
        poc_level = f"{poc_interval.left:.2f} - {poc_interval.right:.2f}"
        poc_volume = volume_profile.max()

        # Конвертация для JSON-сериализации
        volume_profile_str = volume_profile.reset_index()
        volume_profile_str['PRICE'] = volume_profile_str['PRICE'].astype(str)
        
        return {
            'volume_profile': volume_profile_str.to_dict('records'),
            'poc_level': poc_level,
            'poc_volume': float(poc_volume)
        }
        
    def get_large_trades(self, quantile: float = 0.95) -> pd.DataFrame:
        """Поиск крупных сделок выше заданного квантиля по объему."""
        if self.df.empty:
            return pd.DataFrame()
            
        logging.info(f"Поиск крупных сделок (выше {quantile:.0%} квантиля)...")
        large_trade_threshold = self.df['VALUE'].quantile(quantile)
        large_trades = self.df[self.df['VALUE'] >= large_trade_threshold]
        return large_trades[['TRADETIME', 'PRICE', 'QUANTITY', 'VALUE', 'BUYSELL']]
        
    def run_full_analysis(self) -> dict:
        """Запуск всех методов анализа и формирование итогового отчета."""
        logging.info(f"Запуск полного анализа для {self.ticker}...")
        order_flow_df = self.get_order_flow_metrics()
        volume_profile_data = self.get_volume_profile()
        large_trades_df = self.get_large_trades()
        vwap_df = self.get_vwap()

        # Почасовой мини-анализ
        hourly_stats = []
        if not self.df.empty:
            self.df['hour'] = self.df['TRADETIME'].dt.hour
            for hour, group in self.df.groupby('hour'):
                buy_vol = group[group['BUYSELL'] == 'B']['QUANTITY'].sum()
                sell_vol = group[group['BUYSELL'] == 'S']['QUANTITY'].sum()
                delta = buy_vol - sell_vol
                big_trades = group[group['VALUE'] > group['VALUE'].quantile(0.95)]
                direction = 'Покупатели' if delta > 0 else 'Продавцы' if delta < 0 else 'Баланс'
                hourly_stats.append({
                    'hour': f"{hour:02d}:00–{hour+1:02d}:00",
                    'direction': direction,
                    'delta': int(delta),
                    'big_trades': int(len(big_trades)),
                    'buy_vol': int(buy_vol),
                    'sell_vol': int(sell_vol)
                })

        analysis_summary = {
            'ticker': self.ticker,
            'analysis_date': datetime.now().isoformat(),
            'data_period': {
                'start': self.df['TRADETIME'].min().isoformat() if not self.df.empty else None,
                'end': self.df['TRADETIME'].max().isoformat() if not self.df.empty else None,
            },
            'summary_stats': {
                'total_trades': len(self.df),
                'total_volume': float(self.df['QUANTITY'].sum()),
                'buy_volume': float(self.df[self.df['BUYSELL'] == 'B']['QUANTITY'].sum()),
                'sell_volume': float(self.df[self.df['BUYSELL'] == 'S']['QUANTITY'].sum()),
                'delta': float(self.df['signed_volume'].sum()),
                'poc': volume_profile_data['poc_level'],
                'final_vwap': vwap_df['vwap'].iloc[-1] if not vwap_df.empty else None,
            },
            'order_flow_data': order_flow_df.reset_index().to_dict('records'),
            'volume_profile': volume_profile_data['volume_profile'],
            'large_trades': large_trades_df.to_dict('records'),
            'hourly_stats': hourly_stats
        }
        logging.info(f"Анализ для {self.ticker} завершен.")
        return analysis_summary

def load_trades_from_folder(folder_path: str) -> pd.DataFrame | None:
    """Загружает все JSON-файлы со сделками из указанной папки в один DataFrame."""
    if not os.path.isdir(folder_path):
        logging.error(f"Указанный путь не является папкой: {folder_path}")
        return None

    all_trades = []
    for filename in sorted(os.listdir(folder_path)):
        if filename.endswith('.json'):
            file_path = os.path.join(folder_path, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Пропускаем пустые файлы (которые содержат null)
                    if data is None or 'trades' not in data or 'data' not in data['trades']:
                        logging.warning(f"Файл {filename} пуст или имеет неверный формат. Пропускаем.")
                        continue

                    trades_df = pd.DataFrame(data['trades']['data'], columns=data['trades']['columns'])
                    all_trades.append(trades_df)
                    logging.info(f"Загружен файл: {filename}")
            except json.JSONDecodeError:
                logging.error(f"Ошибка декодирования JSON в файле {filename}. Пропускаем.")
            except Exception as e:
                logging.error(f"Ошибка при обработке файла {filename}: {e}")
    
    if not all_trades:
        logging.warning(f"В папке {folder_path} не найдено корректных данных о сделках.")
        return None
        
    df = pd.concat(all_trades, ignore_index=True)
    # Удаляем дубликаты
    if 'TRADENO' in df.columns:
        df = df.drop_duplicates(subset=['TRADENO'])
    else:
        df = df.drop_duplicates(subset=['TRADETIME', 'PRICE', 'QUANTITY', 'BUYSELL'])
    return df

def load_trades_from_file(file_path: str) -> pd.DataFrame | None:
    """Загружает сделки из одного JSON-файла в DataFrame."""
    if not os.path.isfile(file_path):
        logging.error(f"Файл не найден: {file_path}")
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if data is None or 'trades' not in data or 'data' not in data['trades']:
                logging.warning(f"Файл {file_path} пуст или имеет неверный формат.")
                return None
            trades_df = pd.DataFrame(data['trades']['data'], columns=data['trades']['columns'])
            # Удаляем дубликаты
            if 'TRADENO' in trades_df.columns:
                trades_df = trades_df.drop_duplicates(subset=['TRADENO'])
            else:
                trades_df = trades_df.drop_duplicates(subset=['TRADETIME', 'PRICE', 'QUANTITY', 'BUYSELL'])
            return trades_df
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла {file_path}: {e}")
        return None

def load_trades_from_files(file_paths: list[str]) -> pd.DataFrame | None:
    """Загружает сделки из нескольких JSON-файлов в один DataFrame."""
    all_trades = []
    for file_path in file_paths:
        df = load_trades_from_file(file_path)
        if df is not None:
            all_trades.append(df)
    if not all_trades:
        return None
    df = pd.concat(all_trades, ignore_index=True)
    # Удаляем дубликаты
    if 'TRADENO' in df.columns:
        df = df.drop_duplicates(subset=['TRADENO'])
    else:
        df = df.drop_duplicates(subset=['TRADETIME', 'PRICE', 'QUANTITY', 'BUYSELL'])
    return df

if __name__ == '__main__':
    mode = input("Анализировать (1) папку, (2) один файл, (3) несколько файлов? [1/2/3]: ").strip()
    if mode == '1':
        data_folder = input("Введите путь к папке с данными MOEX: ").strip()
        full_df = load_trades_from_folder(data_folder)
    elif mode == '2':
        file_path = input("Введите путь к JSON-файлу: ").strip()
        full_df = load_trades_from_file(file_path)
    elif mode == '3':
        files = input("Введите пути к JSON-файлам через запятую: ").strip().split(',')
        file_paths = [f.strip() for f in files]
        full_df = load_trades_from_files(file_paths)
    else:
        print("Неизвестный режим.")
        full_df = None

    if full_df is not None and not full_df.empty:
        # Анализ по каждому тикеру в данных
        for ticker in full_df['SECID'].unique():
            logging.info(f"\n--- Начинается анализ для тикера: {ticker} ---")
            ticker_df = full_df[full_df['SECID'] == ticker].copy()
            try:
                analyzer = TickerAnalyzer(ticker_df)
                report = analyzer.run_full_analysis()
                output_filename = f'analysis_{ticker}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
                with open(output_filename, 'w', encoding='utf-8') as f:
                    json.dump(report, f, ensure_ascii=False, indent=4, default=str)
                logging.info(f"Анализ для {ticker} завершен. Отчет сохранен в: {output_filename}")
            except Exception as e:
                logging.error(f"Критическая ошибка при анализе тикера {ticker}: {e}", exc_info=True)
    else:
        print("Не найдено данных для анализа.")