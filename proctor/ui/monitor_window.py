"""Standalone CV workspace: no quiz/stub, every Case 3 item has a live status."""
from PyQt6.QtCore import Qt,pyqtSignal
from PyQt6.QtWidgets import (QWidget,QLabel,QVBoxLayout,QHBoxLayout,QPushButton,QGridLayout,
    QScrollArea,QProgressBar,QDoubleSpinBox,QTableWidget,QTableWidgetItem,QHeaderView,QListWidget,QSizePolicy)
from .screens import SidePanel,StatusTiles

CASES=(('PHONE','Телефон: обнаружение'),('HAND','Телефон в руке'),('LIFT','Момент подъёма телефона'),
 ('RAISED','Телефон у лица / экрана'),('AIM','Возможное наведение камеры'),('HEAD','Голова: CENTER / LEFT / RIGHT / UP / DOWN'),
 ('GAZE','Глаза: CENTER / LEFT / RIGHT / UP / DOWN'),('DOWN','Долгий взгляд вниз'),('SIDE','Долгий взгляд влево/вправо'),
 ('PRESENCE','Присутствие / потеря лица'),('MULTI','Второе лицо'),('ALT','Alt+Tab'),('COPY','Ctrl+C/V и буфер'),
 ('WIN','Win'),('SHOT','PrtScn'),('TABS','Вкладки'),('WINDOWS','Сторонние окна/браузеры'))

class MonitorWindow(QWidget):
    calibrate=pyqtSignal(str)
    finish=pyqtSignal()
    def __init__(self):
        super().__init__()
        self.setObjectName('root')
        self.setWindowTitle('Кейс №3 · Прокторинг / CV — без тестовой страницы')
        self.resize(1280,860)
        self.locked=False
        outer=QHBoxLayout(self)
        left=QVBoxLayout()
        self.banner=QLabel('Загрузка локальных моделей…')
        self.banner.setWordWrap(True)
        left.addWidget(self.banner)
        self.camera=QLabel('Нет свежего кадра')
        self.camera.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera.setMinimumSize(500,360)
        self.camera.setObjectName('camera')
        self.camera.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        left.addWidget(self.camera,1)
        self.tiles=StatusTiles()
        self.tiles.setText('HEAD: UNKNOWN\nGAZE: UNKNOWN\nFACE: UNKNOWN\nPHONE: UNKNOWN')
        left.addWidget(self.tiles)
        self.direction_labels={}
        for kind in ('HEAD','GAZE'):
            strip=QHBoxLayout()
            strip.addWidget(QLabel(kind))
            for direction in ('CENTER','LEFT','RIGHT','UP','DOWN'):
                chip=QLabel(direction)
                chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.direction_labels[kind,direction]=chip
                strip.addWidget(chip)
            left.addLayout(strip)
        self.measurements=QLabel('Измерения появятся после первого результата')
        self.measurements.setWordWrap(True)
        left.addWidget(self.measurements)
        self.progress=QProgressBar()
        left.addWidget(self.progress)
        row=QHBoxLayout()
        neutral=QPushButton('Прямо — центр 2с')
        neutral.clicked.connect(lambda:self.calibrate.emit('center'))
        five=QPushButton('Глаза: 5 поз / 20с')
        five.clicked.connect(lambda:self.calibrate.emit('five'))
        stop=QPushButton('Завершить / сохранить')
        stop.clicked.connect(self.finish.emit)
        for button in (neutral,five,stop):
            row.addWidget(button)
        left.addLayout(row)
        outer.addLayout(left,3)
        right=QVBoxLayout()
        knobs=QGridLayout()
        self.phone_threshold=QDoubleSpinBox()
        self.phone_threshold.setRange(.15,.70);self.phone_threshold.setSingleStep(.05);self.phone_threshold.setDecimals(2)
        self.phone_threshold.setValue(.25)
        self.gaze_hold=QDoubleSpinBox()
        self.gaze_hold.setRange(.5,10);self.gaze_hold.setSingleStep(.5);self.gaze_hold.setValue(2)
        self.yaw_threshold=QDoubleSpinBox()
        self.yaw_threshold.setRange(8,45);self.yaw_threshold.setValue(18)
        self.pitch_threshold=QDoubleSpinBox()
        self.pitch_threshold.setRange(8,35);self.pitch_threshold.setValue(12)
        for i,(label,widget) in enumerate((('Phone confidence ≥ (не точность %)',self.phone_threshold),
            ('Удержание GAZE, сек',self.gaze_hold),('HEAD yaw порог, °',self.yaw_threshold),('HEAD pitch порог, °',self.pitch_threshold))):
            knobs.addWidget(QLabel(label),i,0);knobs.addWidget(widget,i,1)
        right.addLayout(knobs)
        self.performance=QLabel('Фактическая частота и возраст результатов: —')
        self.performance.setWordWrap(True)
        right.addWidget(self.performance)
        self.checklist=QTableWidget(len(CASES),2)
        self.checklist.setHorizontalHeaderLabels(['Пункт кейса','Живое состояние'])
        self.checklist.verticalHeader().hide()
        self.checklist.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.checklist.setWordWrap(True)
        self.checklist.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rows={}
        for i,(key,label) in enumerate(CASES):
            self.rows[key]=i
            self.checklist.setItem(i,0,QTableWidgetItem(label))
            self.checklist.setItem(i,1,QTableWidgetItem('UNKNOWN / не измерено'))
        right.addWidget(self.checklist,2)
        self.feed=QListWidget()
        right.addWidget(self.feed,1)
        right_widget=QWidget();right_widget.setLayout(right);right_widget.setMinimumWidth(460)
        outer.addWidget(right_widget,2)
        self.target=QLabel('●',self)
        self.target.setStyleSheet('font-size:36px;color:#75dece;background:transparent;')
        self.target.resize(45,45);self.target.hide()

    def show_target(self,pose):
        coords={'CENTER':(self.width()/2,self.height()/2),'LEFT':(30,self.height()/2),
          'RIGHT':(self.width()-40,self.height()/2),'UP':(self.width()/2,20),'DOWN':(self.width()/2,self.height()-45)}
        x,y=coords[pose];self.target.move(int(x-20),int(y-20));self.target.show();self.target.raise_()

    def show_directions(self,head,gaze):
        for (kind,direction),chip in self.direction_labels.items():
            active=direction==(head if kind=='HEAD' else gaze)
            chip.setStyleSheet('background:#276eae;color:white;padding:3px;border-radius:4px;' if active else 'color:#7d91ab;padding:3px;')

    def show_frame(self,frame):
        self.camera.setPixmap(SidePanel.pixmap(frame,max(500,self.camera.width()),max(360,self.camera.height())))

    def set_case(self,key,text):
        item=self.checklist.item(self.rows[key],1)
        if item.text()!=text:
            item.setText(text)
            self.checklist.resizeRowToContents(self.rows[key])

    def log(self,text):
        self.feed.insertItem(0,text)
        while self.feed.count()>100:
            self.feed.takeItem(self.feed.count()-1)

    def closeEvent(self,event):
        if self.locked:
            event.ignore()
            self.banner.setText('Защищённый режим: выход Ctrl+Shift+F12 с паролем экзаменатора')
        else:
            super().closeEvent(event)
