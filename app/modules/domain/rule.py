from decimal import Decimal
from app.errors import DataError
from .values import whole, spans, union


def evaluate(row):
    start = whole(row['起分钟'], 0, 2880)
    end = whole(row['止分钟'], 0, 2880)
    block = whole(row['每单位时长'], 1, 120)
    if end <= start:
        raise DataError('BUSINESS', '停靠时长须大于零')
    clips = [(max(start, a), min(end, b)) for a, b in spans(row['豁免窗口'])]
    free = sum(b-a for a,b in union((a,b) for a,b in clips if a < b))
    net = end-start-free
    charged = ((net+block-1)//block)*block
    return Decimal(charged), [('原始占用', end-start, '分钟'), ('合计豁免', free, '分钟'), ('豁免后时长', net, '分钟')]
