"""Suite di test di Mnemosine.

Il pacchetto esiste perché i test si importano fra loro (``tests.support`` e
alcuni helper): senza ``__init__.py`` la discovery caricava gli stessi file sia
come ``test_x`` sia come ``tests.test_x``, eseguendo due volte le stesse classi.
"""
