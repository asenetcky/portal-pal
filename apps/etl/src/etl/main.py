from sodapy import Socrata


class domain:
    """open data portal domain"""

    name: str

    # TODO: add methods for pulling domain info down


def pull_domain_data():
    """Pull down domain level metadata"""

    with Socrata("data.ct.gov", None) as client:
        datasets = client.datasets()
        print(datasets[0:5])


def main():
    pull_domain_data()


if __name__ == "__main__":
    main()
