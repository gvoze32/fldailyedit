CC ?= gcc
CFLAGS ?= -O2 -DUSE_PES21_MASTER_KEY -std=c99

SOURCES = src/crypt.c src/mt19937ar.c src/masterkey.c

all: decrypter21 encrypter21

decrypter21: src/decrypter.c $(SOURCES)
	$(CC) $(CFLAGS) src/decrypter.c $(SOURCES) -o decrypter21

encrypter21: src/encrypter.c $(SOURCES)
	$(CC) $(CFLAGS) src/encrypter.c $(SOURCES) -o encrypter21

clean:
	rm -f decrypter21 encrypter21
