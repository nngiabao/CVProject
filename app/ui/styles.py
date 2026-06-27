APP_STYLE = """
QWidget {
    background: #f4f6fb;
    color: #172033;
    font-family: "Segoe UI";
    font-size: 13px;
}
QMainWindow {
    background: #eef2f7;
}
QFrame#header, QFrame#toolbar, QFrame#panel {
    background: #ffffff;
    border: 1px solid #d9e0eb;
    border-radius: 10px;
}
QLabel#title {
    font-size: 22px;
    font-weight: 700;
}
QLabel#subtitle, QLabel#muted {
    color: #69758a;
}
QLabel#metric {
    color: #39465d;
    font-weight: 600;
}
QPushButton {
    background: #f7f9fc;
    border: 1px solid #cfd7e5;
    border-radius: 7px;
    min-height: 34px;
    padding: 0 14px;
    font-weight: 600;
}
QPushButton:hover {
    background: #edf2f9;
}
QPushButton#primary {
    color: #ffffff;
    background: #4169e1;
    border-color: #335bd1;
}
QPushButton#success {
    color: #ffffff;
    background: #198754;
    border-color: #157347;
}
QPushButton#danger {
    color: #ffffff;
    background: #d64550;
    border-color: #bd303a;
}
QPushButton:disabled {
    color: #9aa5b5;
    background: #e8edf5;
    border-color: #d4dbe7;
}
QPushButton#iconButton {
    min-height: 24px;
    padding: 0;
    border-radius: 5px;
}
QSplitter::handle {
    background: #eef2f7;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f7f9fd;
    border: none;
    gridline-color: #e0e6f0;
    selection-background-color: #dbe7ff;
    selection-color: #172033;
}
QHeaderView::section {
    background: #edf2f9;
    color: #39465d;
    border: none;
    border-bottom: 1px solid #d8e0ec;
    padding: 10px;
    font-weight: 700;
}
QPlainTextEdit, QComboBox {
    background: #ffffff;
    border: 1px solid #cfd7e5;
    border-radius: 7px;
    padding: 8px;
}
QStatusBar {
    background: #ffffff;
    color: #69758a;
}
"""
