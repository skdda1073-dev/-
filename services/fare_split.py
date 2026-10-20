"""원 단위 예상요금 배분. 참가자 순서는 호출자가 안정적으로 정한다."""


def split_amount(amount, count):
    if (isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0
            or isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 4):
        return None
    quotient, remainder = divmod(amount, count)
    return [quotient + (index < remainder) for index in range(count)]


def preview(amount):
    return [{"count": count, "amounts_krw": split_amount(amount, count)} for count in range(1, 5)]
