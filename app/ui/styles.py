APP_STYLE = """
QWidget {
    background: #10131f;
    color: #e8eaf2;
    font-family: "Segoe UI";
    font-size: 13px;
}
QMainWindow {
    background: #0c0f19;
}
QFrame#header, QFrame#toolbar, QFrame#panel {
    background: #171b2a;
    border: 1px solid #272d43;
    border-radius: 10px;
}
QLabel#title {
    font-size: 22px;
    font-weight: 700;
}
QLabel#subtitle, QLabel#muted {
    color: #8f97ad;
}
QLabel#metric {
    color: #aab1c5;
    font-weight: 600;
}
QPushButton {
    background: #272d40;
    border: 1px solid #353d56;
    border-radius: 7px;
    min-height: 34px;
    padding: 0 14px;
    font-weight: 600;
}
QPushButton:hover {
    background: #323a52;
}
QPushButton#primary {
    background: #7657f5;
    border-color: #8a70ff;
}
QPushButton#success {
    background: #23834c;
    border-color: #32a864;
}
QPushButton#danger {
    background: #a6373e;
    border-color: #cf4a53;
}
QPushButton:disabled {
    color: #656b7d;
    background: #202433;
    border-color: #2a2f40;
}
QTableWidget {
    background: #121622;
    alternate-background-color: #151a28;
    border: none;
    gridline-color: #252b3e;
    selection-background-color: #3b315f;
}
QHeaderView::section {
    background: #1a1f30;
    color: #b9c0d2;
    border: none;
    border-bottom: 1px solid #30364a;
    padding: 10px;
    font-weight: 700;
}
QPlainTextEdit, QComboBox {
    background: #0f1320;
    border: 1px solid #30364a;
    border-radius: 7px;
    padding: 8px;
}
QStatusBar {
    background: #111521;
    color: #8f97ad;
}
"""
