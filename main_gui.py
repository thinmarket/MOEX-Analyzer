import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QVBoxLayout, QWidget, QLabel, 
    QPushButton, QFileDialog, QTextEdit, QLineEdit, QMessageBox,
    QProgressBar, QHBoxLayout, QCompleter
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from moexparser2 import MOEXDataCollector
from analiz import (
    TickerAnalyzer, 
    load_trades_from_folder, 
    load_trades_from_file, 
    load_trades_from_files
)
from plot_report import plot_report
from report_generator import ReportGenerator
import json
import os
from datetime import datetime
import pandas as pd

# --- Worker Threads ---

class SingleCollectWorker(QThread):
    """Worker-поток для сбора данных по одному тикеру."""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, ticker, market_type):
        super().__init__()
        self.ticker = ticker
        self.market_type = market_type

    def run(self):
        try:
            collector = MOEXDataCollector()
            data = collector.get_trades_data(self.ticker, self.market_type)
            if data:
                collector.save_data(data, self.ticker, 'trades')
                self.finished.emit(f"✔ Данные по {self.ticker} сохранены.")
            else:
                self.error.emit(f"Не удалось получить данные для {self.ticker}.")
        except Exception as e:
            self.error.emit(f"Ошибка при сборе данных для {self.ticker}: {e}")

class AllCollectWorker(QThread):
    """Worker-поток для сбора данных по всем инструментам."""
    log_message = pyqtSignal(str)
    progress_update = pyqtSignal(int, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def run(self):
        try:
            collector = MOEXDataCollector()
            self.log_message.emit("Получение списка инструментов...")
            instruments = collector.get_instruments_list()
            
            all_instruments = instruments['shares'] + instruments['futures']
            total = len(all_instruments)
            self.progress_update.emit(0, total)
            
            num_processed = 0
            for market_type in ['shares', 'futures']:
                for instrument in instruments[market_type]:
                    ticker = instrument['ticker']
                    self.log_message.emit(f"({num_processed+1}/{total}) Сбор данных: {ticker}...")
                    
                    data = collector.get_trades_data(ticker, market_type)
                    if data:
                        collector.save_data(data, ticker, 'trades')
                    
                    num_processed += 1
                    self.progress_update.emit(num_processed, total)
            
            self.finished.emit("✔ Данные по всем инструментам собраны.")
        except Exception as e:
            self.error.emit(f"Критическая ошибка при сборе всех данных: {e}")

# --- Новый: Worker для анализа ---
class AnalysisWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    log = pyqtSignal(str)
    def __init__(self, full_df, json_serializer):
        super().__init__()
        self.full_df = full_df
        self.json_serializer = json_serializer
    def run(self):
        try:
            import json
            import pandas as pd
            tickers = [t for t in self.full_df['SECID'].unique() if pd.notna(t) and t]
            self.progress.emit(0, len(tickers))
            for i, ticker in enumerate(tickers):
                ticker_df = self.full_df[self.full_df['SECID'] == ticker].copy()
                analyzer = TickerAnalyzer(ticker_df)
                report = analyzer.run_full_analysis()
                report_str = json.dumps(report, indent=4, default=self.json_serializer)
                output_filename = f'analysis_{ticker}.json'
                with open(output_filename, 'w', encoding='utf-8') as f:
                    f.write(report_str)
                self.log.emit(f"Анализ для {ticker} сохранен в {output_filename}")
                self.progress.emit(i+1, len(tickers))
            self.finished.emit("Анализ завершен!")
        except Exception as e:
            self.error.emit(str(e))

# --- Новый: Worker для отчёта ---
class ReportWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    def __init__(self, file):
        super().__init__()
        self.file = file
    def run(self):
        try:
            import json
            from report_generator import ReportGenerator
            with open(self.file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            generator = ReportGenerator(data)
            report = generator.generate_full_report()
            self.finished.emit(report)
        except Exception as e:
            self.error.emit(str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MOEX Analyzer")
        self.setGeometry(100, 100, 900, 700)
        self.collect_thread = None

        # Создаем вкладки
        self.tabs = QTabWidget()
        
        # 1. Вкладка сбора данных
        self.data_collector_tab = QWidget()
        self.setup_data_collector_tab()
        self.tabs.addTab(self.data_collector_tab, "Сбор данных")

        # 2. Вкладка анализа
        self.analysis_tab = QWidget()
        self.setup_analysis_tab()
        self.tabs.addTab(self.analysis_tab, "Анализ данных")

        # 3. Вкладка графиков
        self.plot_tab = QWidget()
        self.setup_plot_tab()
        self.tabs.addTab(self.plot_tab, "Графики")

        # 4. Вкладка отчета
        self.report_tab = QWidget()
        self.setup_report_tab()
        self.tabs.addTab(self.report_tab, "Текстовый отчет")

        self.setCentralWidget(self.tabs)

    def set_data_collection_enabled(self, enabled):
        """Включает или выключает элементы управления на вкладке сбора данных."""
        self.btn_get_instruments.setEnabled(enabled)
        self.btn_collect_all.setEnabled(enabled)
        self.btn_collect_selected.setEnabled(enabled)
        self.ticker_input.setEnabled(enabled)

    def setup_data_collector_tab(self):
        layout = QVBoxLayout()

        self.label_data = QLabel("Сбор данных с MOEX")
        self.btn_get_instruments = QPushButton("Получить список инструментов")
        self.btn_collect_all = QPushButton("Собрать данные по всем инструментам")
        self.btn_collect_selected = QPushButton("Собрать данные по выбранному тикеру")
        self.ticker_input = QLineEdit()
        self.ticker_input.setPlaceholderText("Введите тикер или название для поиска...")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        self.btn_get_instruments.clicked.connect(self.load_instruments)
        self.btn_collect_all.clicked.connect(self.collect_all_data)
        self.btn_collect_selected.clicked.connect(self.collect_selected_data)

        layout.addWidget(self.label_data)
        layout.addWidget(self.btn_get_instruments)
        layout.addWidget(self.ticker_input)
        layout.addWidget(self.btn_collect_selected)
        layout.addWidget(self.btn_collect_all)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_output)

        self.data_collector_tab.setLayout(layout)

    def setup_analysis_tab(self):
        layout = QVBoxLayout()

        self.label_analysis = QLabel("Анализ данных")
        self.btn_select_folder = QPushButton("Выбрать папку с данными")
        self.btn_select_files = QPushButton("Выбрать файл(ы) с данными")
        self.btn_run_analysis = QPushButton("Запустить анализ")
        self.analysis_output = QTextEdit()
        self.analysis_output.setReadOnly(True)

        # Прогресс-бар
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setVisible(False)

        self.btn_select_folder.clicked.connect(self.select_data_folder)
        self.btn_select_files.clicked.connect(self.select_data_files)
        self.btn_run_analysis.clicked.connect(self.run_analysis)

        layout.addWidget(self.label_analysis)
        layout.addWidget(self.btn_select_folder)
        layout.addWidget(self.btn_select_files)
        layout.addWidget(self.btn_run_analysis)
        layout.addWidget(self.analysis_progress)
        layout.addWidget(self.analysis_output)

        self.analysis_tab.setLayout(layout)

    def setup_plot_tab(self):
        layout = QVBoxLayout()

        self.label_plot = QLabel("Графики")
        self.btn_select_json = QPushButton("Выбрать JSON для графика")
        self.plot_output = QTextEdit()
        self.plot_output.setReadOnly(True)

        self.btn_select_json.clicked.connect(self.plot_selected_json)

        layout.addWidget(self.label_plot)
        layout.addWidget(self.btn_select_json)
        layout.addWidget(self.plot_output)

        self.plot_tab.setLayout(layout)

    def setup_report_tab(self):
        layout = QVBoxLayout()

        self.label_report = QLabel("Текстовый отчет")
        self.btn_select_report_json = QPushButton("Выбрать JSON для отчета")
        self.report_output = QTextEdit()
        self.report_output.setReadOnly(True)

        self.btn_select_report_json.clicked.connect(self.generate_report)

        layout.addWidget(self.label_report)
        layout.addWidget(self.btn_select_report_json)
        layout.addWidget(self.report_output)

        self.report_tab.setLayout(layout)

    # ===== Методы для вкладки сбора данных =====
    def load_instruments(self):
        self.log_output.append("Загрузка списка инструментов...")
        QApplication.processEvents()  # Обновляем интерфейс

        collector = MOEXDataCollector()
        instruments = collector.get_instruments_list()
        
        self.ticker_type_map = {}
        instrument_list_for_completer = []

        for share in instruments['shares']:
            item_text = f"{share['ticker']} ({share['name']})"
            instrument_list_for_completer.append(item_text)
            self.ticker_type_map[share['ticker']] = 'shares'
        for future in instruments['futures']:
            item_text = f"{future['ticker']} ({future['name']})"
            instrument_list_for_completer.append(item_text)
            self.ticker_type_map[future['ticker']] = 'futures'

        # Настраиваем автодополнение
        completer = QCompleter(instrument_list_for_completer, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains) # Поиск по содержанию
        self.ticker_input.setCompleter(completer)

        self.log_output.append("✔ Список инструментов загружен.")

    def collect_all_data(self):
        self.set_data_collection_enabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.log_output.setText("Начало сбора данных по всем инструментам...")

        self.collect_thread = AllCollectWorker()
        self.collect_thread.log_message.connect(self.log_output.append)
        self.collect_thread.progress_update.connect(lambda v, m: (self.progress_bar.setMaximum(m), self.progress_bar.setValue(v)))
        self.collect_thread.finished.connect(self.on_collect_finished)
        self.collect_thread.error.connect(self.on_collect_error)
        self.collect_thread.start()

    def collect_selected_data(self):
        ticker_text = self.ticker_input.text()
        if not ticker_text:
            QMessageBox.warning(self, "Ошибка", "Введите тикер.")
            return

        ticker = ticker_text.split()[0]
        
        if not hasattr(self, 'ticker_type_map') or ticker not in self.ticker_type_map:
            QMessageBox.warning(self, "Ошибка", f"Тикер '{ticker}' не найден. Сначала загрузите список инструментов.")
            return
            
        market_type = self.ticker_type_map.get(ticker)
        
        self.set_data_collection_enabled(False)
        self.progress_bar.setMaximum(0)
        self.progress_bar.setVisible(True)
        self.log_output.append(f"Сбор данных для {ticker} ({'Фьючерс' if market_type == 'futures' else 'Акция'})...")

        self.collect_thread = SingleCollectWorker(ticker, market_type)
        self.collect_thread.finished.connect(self.on_collect_finished)
        self.collect_thread.error.connect(self.on_collect_error)
        self.collect_thread.start()

    # ===== Методы для вкладки анализа =====
    def select_data_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с данными")
        if folder:
            self.analysis_output.append(f"Выбрана папка: {folder}")
            self.data_folder = folder
            if hasattr(self, 'data_files'):
                del self.data_files  # чтобы не было конфликта выбора

    def select_data_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите один или несколько JSON-файлов", "", "JSON Files (*.json)")
        if files:
            self.analysis_output.append(f"Выбраны файлы: {', '.join(files)}")
            self.data_files = files
            if hasattr(self, 'data_folder'):
                del self.data_folder  # чтобы не было конфликта выбора

    def run_analysis(self):
        # Определяем источник данных
        full_df = None
        if hasattr(self, 'data_files'):
            if len(self.data_files) == 1:
                full_df = load_trades_from_file(self.data_files[0])
            else:
                full_df = load_trades_from_files(self.data_files)
        elif hasattr(self, 'data_folder'):
            full_df = load_trades_from_folder(self.data_folder)
        else:
            QMessageBox.warning(self, "Ошибка", "Сначала выберите папку или файлы с данными!")
            return

        if full_df is None or full_df.empty:
            self.analysis_output.append("Ошибка загрузки данных.")
            return

        self.analysis_progress.setVisible(True)
        self.analysis_progress.setMaximum(0)
        self.analysis_output.append("Запуск анализа...")
        QApplication.processEvents()

        try:
            tickers = [t for t in full_df['SECID'].unique() if pd.notna(t) and t]
            self.analysis_progress.setMaximum(len(tickers))

            for i, ticker in enumerate(tickers):
                ticker_df = full_df[full_df['SECID'] == ticker].copy()
                analyzer = TickerAnalyzer(ticker_df)
                report = analyzer.run_full_analysis()
                report_str = json.dumps(report, indent=4, default=self.json_serializer)
                output_filename = f'analysis_{ticker}.json'
                with open(output_filename, 'w', encoding='utf-8') as f:
                    f.write(report_str)
                self.analysis_output.append(f"Анализ для {ticker} сохранен в {output_filename}")
                self.analysis_progress.setValue(i + 1)
                QApplication.processEvents()

            QMessageBox.information(self, "Готово", "Анализ завершен!")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка при анализе: {str(e)}")
        finally:
            self.analysis_progress.setVisible(False)

    def on_collect_finished(self, message):
        """Слот, вызываемый по завершении сбора данных."""
        self.log_output.append(message)
        if self.progress_bar.maximum() != 0:
            self.progress_bar.setValue(self.progress_bar.maximum())
        QMessageBox.information(self, "Готово", "Сбор данных завершен!")
        self.set_data_collection_enabled(True)
        self.progress_bar.setVisible(False)
        self.collect_thread = None

    def on_collect_error(self, message):
        """Слот, вызываемый при ошибке сбора данных."""
        self.log_output.append(f"❌ {message}")
        QMessageBox.critical(self, "Ошибка", message)
        self.set_data_collection_enabled(True)
        self.progress_bar.setVisible(False)
        self.collect_thread = None

    def json_serializer(self, obj):
        """Преобразует Timestamp и другие объекты в строку для JSON."""
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        raise TypeError(f"Объект типа {type(obj)} не сериализуем")

    # ===== Методы для вкладки графиков =====
    def plot_selected_json(self):
        file, _ = QFileDialog.getOpenFileName(self, "Выберите JSON файл", "", "JSON Files (*.json)")
        if file:
            self.plot_output.append("Построение графика...")
            try:
                plot_report(file)
                self.plot_output.append(f"График построен для файла: {file}")
                QMessageBox.information(self, "Готово", "График успешно создан!")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Ошибка при построении графика: {str(e)}")

    # ===== Методы для вкладки отчета =====
    def generate_report(self):
        file, _ = QFileDialog.getOpenFileName(self, "Выберите JSON файл", "", "JSON Files (*.json)")
        if file:
            self.report_output.setPlainText("Генерация отчёта...")
            self.report_thread = ReportWorker(file)
            self.report_thread.finished.connect(lambda report: (self.report_output.setPlainText(report), QMessageBox.information(self, "Готово", "Отчет сгенерирован!")))
            self.report_thread.error.connect(lambda msg: QMessageBox.critical(self, "Ошибка", f"Ошибка при генерации отчета: {msg}"))
            self.report_thread.start()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # --- QSS стилизация ---
    app.setStyleSheet('''
        QMainWindow {
            background-color: #181818;
        }
        QWidget {
            background-color: #181818;
            color: #f8f8f2;
        }
        QLabel {
            color: #f8f8f2;
        }
        QPushButton {
            background-color: #232323;
            color: #f8f8f2;
            border: 1px solid #444;
            border-radius: 6px;
            padding: 6px 12px;
        }
        QPushButton:hover {
            background-color: #2d2d2d;
            color: #f8f8f2;
            border: 1px solid #2f4538;
        }
        QPushButton:pressed {
            background-color: #2f4538;
            color: #f8f8f2;
        }
        QLineEdit, QTextEdit {
            background-color: #232323;
            color: #f8f8f2;
            border: 1px solid #444;
            border-radius: 6px;
        }
        QProgressBar {
            background-color: #232323;
            color: #f8f8f2;
            border: 1px solid #444;
            border-radius: 6px;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: #2f4538;
            color: #f8f8f2;
            border-radius: 6px;
        }
        QTabWidget::pane {
            border: 1px solid #444;
            background: #181818;
        }
        QTabBar::tab {
            background: #232323;
            color: #f8f8f2;
            border: 1px solid #444;
            border-bottom: none;
            border-radius: 6px 6px 0 0;
            padding: 6px 12px;
        }
        QTabBar::tab:selected {
            background: #2f4538;
            color: #f8f8f2;
        }
        QTabBar::tab:hover {
            background: #2d2d2d;
            color: #f8f8f2;
        }
        QMessageBox {
            background-color: #232323;
            color: #f8f8f2;
        }
    ''')
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())