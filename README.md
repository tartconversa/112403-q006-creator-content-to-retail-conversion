# 达人内容长短链路评估与合作续约依据

合成达人内容、平台互动、匿名交易及合同结算样例。金额为人民币元。

All example identifiers and values are synthetic. CSV files use UTF-8. The first row contains column names, and an empty cell means the source supplied no value.

## Sample files

- `content.csv` contains 达人发布内容、渠道和活动时间。 Columns are `post`, `creator`, `channel`, `campaign`, `published`.
- `signals.csv` contains 互动和平台曝光汇总及订单归因记录。 Columns are `record`, `post`, `metric`, `value`, `window`.
- `contracts.csv` contains 固定费用、佣金和退款调整。 Columns are `creator`, `fixed`, `commission`, `period`.
- `orders.csv` contains 匿名订单、归因触点及退款状态。 Columns are `order_id`, `person`, `post`, `paid`, `refund`, `state`.

Python 3 standard library is sufficient to inspect these inputs. Run `python3 -m unittest discover -s tests -v` to check the delivered file structures and example references.
