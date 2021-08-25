from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QFont, QMovie


class Tutorial(QDialog):
    pages = [
        """
        Bienvenu dans le client du Bordeaux 3, une nouvelle variante de poker imaginee et creee en 2021.

        Cette variante, proche du NL Texas hold'em, n'en differe que par trois points:

        1. Les cartes communes.
        
        Le flop le flop comporte 4 cartes divisees en un flop haut et flop bas, de chacun deux cartes.
        
        La turn est egalement une carte doublee en haut et en bas.
        
        La river est une seul carte commune pouvant etre utilisee dans toutes les mains.
        """,

        """2. Les cartes cachees
        Trois cartes sont distribuees. 
        
        Sur ces trois cartes, exactement deux seront utilisees pour former la main finale, et exactement 3 du board.
         
        Par conséquent, si le board montre un carre, il n'est pas possible d'avoir ce carré. 
        
        De mêmè un brelan servi est considéré comme une main faible car il ne s'agit que d'une paire avec une carte en moins pour ameliorer.
        """,

        """3. Les encheres
        Le jeu est No Limit, sauf le preflop qui est en mises fixes.
        
        Ces mises fixes augmentent progressivement selon un schema fixe, la somme des deux dernieres mises realisees.
        
        La premiere mise est donc une relance de 15 en plus du montant du call, soit 25 au total. 
        
        Les sur-relances suivent alors une montee progressive: +15, puis +25, puis +40, +65 etc.
        
        Seul le preflop est affecte. Dans les autres rounds, les règles sont celles du no limit.
        """
    ]
    footers = [
        """
        Les chemins en rouge indiquent les 4 différents board de 4 cartes qui peuvent être utilisés pour former la meilleure main de 5 cartes. 
        
        Il n'est pas possible d'utiliser à la fois des cartes du flop haut et bas, ou les deux turns.
        """,
        "",
        '',
    ]
    images = [
        "board.gif",
        "showdown.png",
        "preflop.png",
    ]

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.text = QLabel()
        self.footer = QLabel()
        self.image = QLabel()
        self.current_page = 0
        self._update_page()
        self.layout.addWidget(self.text)
        self.layout.addWidget(self.image)
        self.layout.addWidget(self.footer)
        self.next_btn = QPushButton()
        self.next_btn.setText("Suivant")
        self.layout.addWidget(self.next_btn)
        self.next_btn.pressed.connect(self.next_page)
        self.setFixedSize(800, 800)
        self.layout.setAlignment(Qt.AlignHCenter)

    def _update_page(self):
        self.text.setText(self.pages[self.current_page])
        self.footer.setText(self.footers[self.current_page])
        if self.images[self.current_page].endswith('gif'):
            self.movie = QMovie(f"images/{self.images[self.current_page]}")
            self.image.setMovie(self.movie)
            self.movie.start()
        else:
            pxmap = QPixmap()
            pxmap.load(f"images/{self.images[self.current_page]}")
            self.image.setPixmap(pxmap)

    def next_page(self):
        self.current_page += 1
        if self.current_page == len(self.pages) - 1:
            self.next_btn.setText("Termine")
        if self.current_page == len(self.pages):
            self.done(0)
            return
        self._update_page()