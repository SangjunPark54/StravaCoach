import truststore

truststore.inject_into_ssl()

from .cli import main

if __name__ == "__main__":
    main()
