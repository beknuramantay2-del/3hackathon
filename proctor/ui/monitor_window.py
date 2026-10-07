"""An operator-friendly desktop monitor: observation first, diagnostics one tab away."""
from PyQt6.QtCore import Qt,pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QWidget,QLabel,QFrame,QVBoxLayout,QHBoxLayout,QPushButton,QGridLayout,
    QProgressBar,QDoubleSpinBox,QTableWidget,QTableWidgetItem,QHeaderView,QListWidget,QSizePolicy,QTabWidget,QSplitter)
from .screens import SidePanel

DIRECTIONS={'CENTER':'Прямо','LEFT':'Влево','RIGHT':'Вправо','UP':'Вверх','DOWN':'Вниз','UNKNOWN':'Не определяется'}
ARROWS={'CENTER':'●','LEFT':'←','RIGHT':'→','UP':'↑','DOWN':'↓','UNKNOWN':'—'}
CASES=(('PHONE','Телефон в кадре'),('HAND','Телефон в руке'),('LIFT','Подъём телефона'),
 ('RAISED','Телефон у лица / экрана'),('AIM','Возможное наведение камеры'),('HEAD','Направление головы'),
 ('GAZE','Направление глаз'),('DOWN','Длительный взгляд вниз'),('SIDE','Длительный взгляд в сторону'),
 ('PRESENCE','Присутствие ученика'),('MULTI','Второе лицо'),('ALT','Alt+Tab'),('COPY','Копирование / вставка'),
 ('WIN','Клавиша Win'),('SHOT','Снимок экрана'),('TABS','Переключение вкладок'),('WINDOWS','Посторонние окна'))

class VideoPreview(QLabel):
    def __init__(self):
        super().__init__('Ожидаем изображение с камеры…')
        self.setObjectName('camera');self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(420,220)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        self.hud=QLabel('ГЛАЗА — Не определяется\nГОЛОВА — Не определяется',self)
        self.hud.setObjectName('cameraHud');self.hud.setWordWrap(True)
        self.hud.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.hud.setGeometry(12,max(12,self.height()-78),max(100,self.width()-24),64)

class DirectionCard(QFrame):
    def __init__(self,title):
        super().__init__();self.setObjectName('panel')
        layout=QVBoxLayout(self);layout.setContentsMargins(16,12,16,12);layout.setSpacing(6)
        title_label=QLabel(title);title_label.setObjectName('eyebrow');layout.addWidget(title_label)
        self.state=QLabel('— Не определяется');self.state.setObjectName('directionValue');layout.addWidget(self.state)
        strip=QHBoxLayout();self.chips={}
        for direction in ('LEFT','UP','CENTER','DOWN','RIGHT'):
            chip=QLabel(ARROWS[direction]);chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setToolTip(DIRECTIONS[direction]);chip.setAccessibleName(DIRECTIONS[direction])
            self.chips[direction]=chip;strip.addWidget(chip)
        layout.addLayout(strip)
        self.note=QLabel('Нет свежего измерения');self.note.setObjectName('muted');self.note.setWordWrap(True);layout.addWidget(self.note)
    def update_state(self,state):
        state=state if state in DIRECTIONS else 'UNKNOWN'
        self.state.setText(ARROWS[state]+' '+DIRECTIONS[state])
        for direction,chip in self.chips.items():
            active=direction==state
            chip.setStyleSheet('background:#244a78;color:#e4f0ff;border:1px solid #679ee6;border-radius:5px;font-size:20px;padding:2px;' if active else 'color:#8796ad;font-size:20px;padding:2px;')

class MonitorWindow(QWidget):
    calibrate=pyqtSignal(str)
    finish=pyqtSignal()
    def __init__(self):
        super().__init__();self.setObjectName('root')
        self.setWindowTitle('Прокторинг · Камера и наблюдение')
        self.resize(1280,800);self.setMinimumSize(960,700);self.locked=False
        outer=QVBoxLayout(self);outer.setContentsMargins(24,18,24,18);outer.setSpacing(14)
        header=QHBoxLayout()
        heading=QVBoxLayout();title=QLabel('Наблюдение за экзаменом');title.setObjectName('title')
        heading.addWidget(title);subtitle=QLabel('Локально на устройстве · камера, глаза и предметы');subtitle.setObjectName('muted');heading.addWidget(subtitle)
        header.addLayout(heading,1)
        self.mode_label=QLabel('Просмотр CV · защита выключена');self.mode_label.setObjectName('modeBadge');header.addWidget(self.mode_label)
        self.session_clock=QLabel('00:00');self.session_clock.setObjectName('clock');header.addWidget(self.session_clock)
        outer.addLayout(header)
        split=QSplitter(Qt.Orientation.Horizontal);split.setChildrenCollapsible(False)
        left_widget=QWidget();left=QVBoxLayout(left_widget);left.setContentsMargins(0,0,16,0);left.setSpacing(10)
        self.banner=QLabel('Подготовка камеры и локальных моделей…');self.banner.setObjectName('banner');self.banner.setWordWrap(True);left.addWidget(self.banner)
        self.camera=VideoPreview();left.addWidget(self.camera,1)
        presence=QHBoxLayout();self.face_summary=QLabel('Лицо: ожидаем измерение');self.phone_summary=QLabel('Телефон: ожидаем измерение')
        for label in (self.face_summary,self.phone_summary):label.setObjectName('observationBadge');presence.addWidget(label,1)
        left.addLayout(presence)
        cards=QHBoxLayout();self.head_card=DirectionCard('ГОЛОВА');self.gaze_card=DirectionCard('ГЛАЗА')
        cards.addWidget(self.head_card);cards.addWidget(self.gaze_card);left.addLayout(cards)
        self.direction_labels={(kind,d):card.chips[d] for kind,card in (('HEAD',self.head_card),('GAZE',self.gaze_card)) for d in card.chips}
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.setFormat('Нет активного удержания');left.addWidget(self.progress)
        self.calibration_note=QLabel('Для настройки взгляда сядьте прямо и следуйте пяти точкам. Это займёт 10 секунд.');self.calibration_note.setObjectName('muted');self.calibration_note.setWordWrap(True);left.addWidget(self.calibration_note)
        buttons=QHBoxLayout();self.neutral=QPushButton('Обновить центр · 2 с');self.neutral.setObjectName('secondary');self.five=QPushButton('Настроить взгляд · 10 с')
        self.neutral.clicked.connect(lambda:self.calibrate.emit('center'));self.five.clicked.connect(lambda:self.calibrate.emit('five'))
        buttons.addWidget(self.five);buttons.addWidget(self.neutral);left.addLayout(buttons)
        split.addWidget(left_widget)
        right_widget=QWidget();right_widget.setMinimumWidth(330);right=QVBoxLayout(right_widget);right.setContentsMargins(0,0,0,0);right.setSpacing(12)
        self.tabs=QTabWidget();right.addWidget(self.tabs,1)
        events=QWidget();el=QVBoxLayout(events);el.setContentsMargins(12,16,12,12);el.setSpacing(12)
        hint=QLabel('Наблюдение, а не автоматический вердикт');hint.setObjectName('sectionTitle');hint.setWordWrap(True);el.addWidget(hint)
        note=QLabel('Короткий взгляд в сторону не создаёт событие. Длительное удержание видно на шкале под камерой.');note.setObjectName('muted');note.setWordWrap(True);el.addWidget(note)
        self.feed=QListWidget();self.feed.setWordWrap(True);self.feed.setAccessibleName('Лента событий');el.addWidget(self.feed,1)
        self.empty_feed=QLabel('Событий пока нет');self.empty_feed.setObjectName('muted');el.addWidget(self.empty_feed)
        self.tabs.addTab(events,'События')
        requirements=QWidget();rl=QVBoxLayout(requirements);rl.setContentsMargins(8,12,8,8)
        info=QLabel('Живые статусы всех пунктов кейса. Выключенная защита не считается работающей.');info.setWordWrap(True);info.setObjectName('muted');rl.addWidget(info)
        self.checklist=QTableWidget(len(CASES),2);self.checklist.setHorizontalHeaderLabels(['Контроль','Состояние'])
        self.checklist.verticalHeader().hide();self.checklist.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.checklist.setWordWrap(True);self.checklist.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.checklist.setSelectionMode(QTableWidget.SelectionMode.NoSelection);self.checklist.setAlternatingRowColors(True)
        self.rows={}
        for i,(key,label) in enumerate(CASES):
            self.rows[key]=i;self.checklist.setItem(i,0,QTableWidgetItem(label));self.checklist.setItem(i,1,QTableWidgetItem('Не измерено'))
        rl.addWidget(self.checklist);self.tabs.addTab(requirements,'Кейс №3')
        diagnostic=QWidget();dl=QVBoxLayout(diagnostic);dl.setContentsMargins(12,16,12,12);dl.setSpacing(12)
        self.measurements=QLabel('Измерения появятся после первого результата');self.measurements.setWordWrap(True);self.measurements.setObjectName('diagnosticText');dl.addWidget(self.measurements)
        self.performance=QLabel('Частота и возраст измерений: —');self.performance.setWordWrap(True);self.performance.setObjectName('diagnosticText');dl.addWidget(self.performance)
        knobs=QGridLayout();knobs.setVerticalSpacing(8)
        self.phone_threshold=self.spin(.15,.70,.05,.25,2)
        self.gaze_hold=self.spin(.5,10,.5,2,1);self.down_hold=self.spin(.5,10,.5,3,1)
        self.yaw_threshold=self.spin(8,45,1,18,0);self.pitch_threshold=self.spin(8,35,1,12,0)
        for i,(label,widget) in enumerate((('Порог уверенного телефона',self.phone_threshold),('Взгляд в сторону, с',self.gaze_hold),('Взгляд вверх / вниз, с',self.down_hold),('Поворот головы, °',self.yaw_threshold),('Наклон головы, °',self.pitch_threshold))):
            l=QLabel(label);l.setWordWrap(True);knobs.addWidget(l,i,0);knobs.addWidget(widget,i,1)
        dl.addLayout(knobs)
        explanation=QLabel('Confidence — оценка модели, не точность в %. Слабый телефон требует повторных наблюдений и руки либо согласия общего поиска и ROI. Изменение порогов влияет на события.');explanation.setWordWrap(True);explanation.setObjectName('muted');dl.addWidget(explanation);dl.addStretch(1)
        self.tabs.addTab(diagnostic,'Настройки')
        self.stop=QPushButton('Завершить и сохранить');self.stop.setObjectName('secondary');self.stop.clicked.connect(self.finish.emit);right.addWidget(self.stop)
        split.addWidget(right_widget);split.setStretchFactor(0,3);split.setStretchFactor(1,2);split.setSizes([740,430]);outer.addWidget(split,1)
        self.target=QLabel('●',self);self.target.setObjectName('calibrationTarget');self.target.resize(48,48);self.target.setAlignment(Qt.AlignmentFlag.AlignCenter);self.target.hide()
        self._pose=None
        self._warning=False

    @staticmethod
    def spin(low,high,step,value,decimals):
        w=QDoubleSpinBox();w.setRange(low,high);w.setSingleStep(step);w.setDecimals(decimals);w.setValue(value);w.setMinimumHeight(38);return w

    def show_target(self,pose):
        self._pose=pose;w,h=self.width(),self.height();pad=36
        coords={'CENTER':(w/2,h/2),'LEFT':(pad,h/2),'RIGHT':(w-pad,h/2),'UP':(w/2,pad),'DOWN':(w/2,h-pad)}
        x,y=coords[pose];self.target.move(int(x-24),int(y-24));self.target.show();self.target.raise_()

    def calibration_running(self,pose,remaining,total):
        self.neutral.setEnabled(False);self.five.setEnabled(False)
        self.calibration_note.setText(f'{ARROWS[pose]} {DIRECTIONS[pose]}: смотрите на точку, голова неподвижна · осталось {remaining:.0f} с')
        self.progress.setValue(int(100*(1-remaining/max(total,.01))));self.progress.setFormat('Настройка взгляда — следуйте точке')

    def calibration_finished(self,message):
        self.neutral.setEnabled(True);self.five.setEnabled(True);self.target.hide();self._pose=None;self.calibration_note.setText(message)

    def show_directions(self,head,gaze,calibrated=True):
        self.head_card.update_state(head);self.gaze_card.update_state(gaze)
        self.head_card.note.setText('Отдельно от движения глаз' if calibrated else 'Сначала настройте центральную позу')
        self.gaze_card.note.setText('Измерение положения зрачков' if calibrated else 'Предварительно · предупреждения выключены')
        self.camera.hud.setText(f'ГЛАЗА {ARROWS.get(gaze,"—")} {DIRECTIONS.get(gaze,"Не определяется")}\nГОЛОВА {ARROWS.get(head,"—")} {DIRECTIONS.get(head,"Не определяется")}'+(' · нужна настройка' if not calibrated else ''))

    def show_frame(self,frame):
        self.camera.setPixmap(SidePanel.pixmap(frame,self.camera.width(),self.camera.height()))
        if self._pose:self.show_target(self._pose)

    def highlight_gaze(self,warning):
        warning=bool(warning)
        if warning==self._warning:return
        self._warning=warning
        for control in (self.gaze_card.state,self.progress):
            control.setProperty('warning',warning)
            control.style().unpolish(control);control.style().polish(control);control.update()

    def set_observation(self,faces,phone):
        self.face_summary.setText('Лицо: '+str(faces));self.phone_summary.setText('Телефон: '+phone)

    def set_case(self,key,text):
        item=self.checklist.item(self.rows[key],1)
        if item.text()!=text:
            item.setText(text)
            color='#9ba9bd' if any(k in text for k in ('UNKNOWN','НЕ АКТИВНО','Нет браузера','Не измерено','Не определяется','предварительно')) else '#efbb72' if any(k in text for k in ('АКТИВНО','CANDIDATE','эвристика')) else '#dce5f2'
            item.setForeground(QColor(color));self.checklist.resizeRowToContents(self.rows[key])

    def log(self,text):
        self.empty_feed.hide();self.feed.insertItem(0,text)
        while self.feed.count()>100:self.feed.takeItem(self.feed.count()-1)

    def closeEvent(self,event):
        if self.locked:
            event.ignore();self.banner.setText('Защищённый режим: выход Ctrl+Shift+F12 с паролем экзаменатора')
        else:super().closeEvent(event)
