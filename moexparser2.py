import requests
import json
import schedule
import time
from datetime import datetime, timedelta
import os
import logging
from threading import Thread

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('moex_data_collector.log'),
        logging.StreamHandler()
    ]
)

class MOEXDataCollector:
    def __init__(self):
        self.data_dir = "moex_data"
        os.makedirs(self.data_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'MOEX Data Collector/1.0'})
        
    def get_instruments_list(self):
        """Получение списка всех инструментов с MOEX"""
        instruments = {'shares': [], 'futures': []}
        
        try:
            # Акции (TQBR)
            shares_url = "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json?iss.meta=off&securities.columns=SECID,SHORTNAME"
            response = self.session.get(shares_url)
            response.raise_for_status()
            data = response.json()
            
            instruments['shares'] = [
                {'ticker': item[0], 'name': item[1]} 
                for item in data['securities']['data']
                if len(item) >= 2
            ]
            
            # Фьючерсы (FORTS)
            futures_url = "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?iss.meta=off&securities.columns=SECID,SECNAME,MATDATE"
            response = self.session.get(futures_url)
            response.raise_for_status()
            data = response.json()
            
            instruments['futures'] = [
                {'ticker': item[0], 'name': item[1], 'expiration': item[2] if len(item) > 2 else None} 
                for item in data['securities']['data']
                if len(item) >= 1
            ]
            
            logging.info(f"Получено {len(instruments['shares'])} акций и {len(instruments['futures'])} фьючерсов")
            
        except Exception as e:
            logging.error(f"Ошибка при загрузке списка инструментов: {e}")
            # Возвращаем пустой список в случае ошибки
            return {'shares': [], 'futures': []}
            
        return instruments
    
    def get_trades_data(self, ticker, market_type='shares'):
        """Получение всех данных о сделках для конкретного инструмента за день (с пагинацией)."""
        all_data = None
        start = 0
        page_size = 1000
        while True:
            try:
                if market_type == 'shares':
                    url = f'https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}/trades.json?start={start}'
                else:
                    url = f'https://iss.moex.com/iss/engines/futures/markets/forts/securities/{ticker}/trades.json?start={start}'
                response = self.session.get(url)
                response.raise_for_status()
                data = response.json()
                # Проверяем, есть ли данные
                trades = data.get('trades', {})
                if not trades or not trades.get('data'):
                    break
                if all_data is None:
                    all_data = data
                else:
                    # Добавляем новые сделки к уже собранным
                    all_data['trades']['data'].extend(trades['data'])
                # Если получено меньше page_size, значит это последняя страница
                if len(trades['data']) < page_size:
                    break
                start += page_size
            except Exception as e:
                logging.error(f'Ошибка при получении данных по сделкам для {ticker}: {e}')
                break
        return all_data
    
    def save_data(self, data, ticker, data_type):
        """Сохранение данных в файл с организацией по датам"""
        today = datetime.now().strftime("%Y-%m-%d")
        date_dir = os.path.join(self.data_dir, today)
        os.makedirs(date_dir, exist_ok=True)
        
        filename = os.path.join(date_dir, f"{ticker}_{data_type}_{datetime.now().strftime('%H%M')}.json")
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logging.info(f'Данные сохранены в файл: {filename}')
        except Exception as e:
            logging.error(f'Ошибка при сохранении файла {filename}: {e}')
    
    def collect_all_data(self):
        """Сбор данных по всем инструментам"""
        logging.info("Начало сбора данных...")
        instruments = self.get_instruments_list()
        
        for market_type in ['shares', 'futures']:
            for instrument in instruments[market_type]:
                ticker = instrument['ticker']
                logging.info(f"Обрабатывается {ticker} ({market_type})...")
                
                data = self.get_trades_data(ticker, market_type)
                if data:
                    self.save_data(data, ticker, 'trades')
                
                # Добавьте здесь другие типы данных, которые нужно собирать
                # Например, стаканы котировок, исторические данные и т.д.
                
                # Небольшая задержка между запросами
                time.sleep(1)
        
        logging.info("Сбор данных завершен")
    
    def run_scheduled(self):
        """Запуск по расписанию"""
        # Настройка расписания
        schedule.every().day.at("09:30").do(self.collect_all_data)  # Перед открытием
        schedule.every().day.at("18:45").do(self.collect_all_data)  # После закрытия
        
        # Для тестирования - каждые 10 минут
        # schedule.every(10).minutes.do(self.collect_all_data)
        
        logging.info("Сервис запущен. Ожидание расписания...")
        
        while True:
            schedule.run_pending()
            time.sleep(60)

if __name__ == '__main__':
    collector = MOEXDataCollector()
    scheduler_thread = Thread(target=collector.run_scheduled)
    scheduler_thread.daemon = True
    scheduler_thread.start()

    while True:
        print("\nМеню управления:")
        print("1 - Показать список инструментов")
        print("2 - Загрузить сделки по выбранному тикеру")
        print("3 - Загрузить сделки по всем инструментам")
        print("4 - Выход")
        choice = input("Выберите действие: ").strip()

        if choice == '1':
            instruments = collector.get_instruments_list()
            print("\nАкции:")
            for share in instruments['shares']:
                print(f"{share['ticker']} - {share['name']}")
            print("\nФьючерсы:")
            for future in instruments['futures']:
                expiration_str = f" (до {future['expiration']})" if future.get('expiration') else ""
                print(f"{future['ticker']} - {future['name']}{expiration_str}")
        elif choice == '2':
            instruments = collector.get_instruments_list()
            all_tickers = {item['ticker'].upper(): ('shares', item['name']) for item in instruments['shares']}
            all_tickers.update({item['ticker'].upper(): ('futures', item['name']) for item in instruments['futures']})
            print("\nДоступные тикеры:")
            for ticker, (market, name) in all_tickers.items():
                print(f"{ticker} - {name} ({'Акция' if market == 'shares' else 'Фьючерс'})")
            selected = input("Введите тикер для загрузки сделок: ").strip().upper()
            if selected in all_tickers:
                market_type = all_tickers[selected][0]
                print(f"Загружаю сделки по {selected}...")
                data = collector.get_trades_data(selected, market_type)
                if data:
                    collector.save_data(data, selected, 'trades')
                    print(f"Данные по {selected} успешно сохранены.")
                else:
                    print(f"Не удалось получить данные по {selected}.")
            else:
                print("Тикер не найден в списке.")
        elif choice == '3':
            collector.collect_all_data()
        elif choice == '4':
            logging.info("Завершение работы...")
            break
        else:
            print("Неверный выбор. Попробуйте снова.")