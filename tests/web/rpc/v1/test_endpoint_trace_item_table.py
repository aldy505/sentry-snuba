import random
import uuid
from datetime import datetime, timedelta
from typing import Any, Mapping

import pytest
from google.protobuf.json_format import MessageToDict
from google.protobuf.timestamp_pb2 import Timestamp
from sentry_protos.snuba.v1.endpoint_trace_item_table_pb2 import (
    Column,
    TraceItemColumnValues,
    TraceItemTableRequest,
    TraceItemTableResponse,
)
from sentry_protos.snuba.v1.error_pb2 import Error as ErrorProto
from sentry_protos.snuba.v1.request_common_pb2 import PageToken, RequestMeta
from sentry_protos.snuba.v1.trace_item_attribute_pb2 import (
    AttributeAggregation,
    AttributeKey,
    AttributeValue,
    Function,
    VirtualColumnContext,
)
from sentry_protos.snuba.v1.trace_item_filter_pb2 import (
    ComparisonFilter,
    ExistsFilter,
    OrFilter,
    TraceItemFilter,
)

from snuba.datasets.storages.factory import get_storage
from snuba.datasets.storages.storage_key import StorageKey
from snuba.web.rpc.common.exceptions import BadSnubaRPCRequestException
from snuba.web.rpc.v1.endpoint_trace_item_table import EndpointTraceItemTable
from tests.base import BaseApiTest
from tests.helpers import write_raw_unprocessed_events

_RELEASE_TAG = "backend@24.7.0.dev0+c45b49caed1e5fcbf70097ab3f434b487c359b6b"
_SERVER_NAME = "D23CXQ4GK2.local"


def gen_message(
    dt: datetime,
    measurements: dict[str, dict[str, float]] | None = None,
    tags: dict[str, str] | None = None,
) -> Mapping[str, Any]:
    measurements = measurements or {}
    tags = tags or {}
    return {
        "description": "/api/0/relays/projectconfigs/",
        "duration_ms": 152,
        "event_id": "d826225de75d42d6b2f01b957d51f18f",
        "exclusive_time_ms": 0.228,
        "is_segment": True,
        "data": {
            "sentry.environment": "development",
            "sentry.release": _RELEASE_TAG,
            "thread.name": "uWSGIWorker1Core0",
            "thread.id": "8522009600",
            "sentry.segment.name": "/api/0/relays/projectconfigs/",
            "sentry.sdk.name": "sentry.python.django",
            "sentry.sdk.version": "2.7.0",
            "my.float.field": 101.2,
            "my.int.field": 2000,
            "my.neg.field": -100,
            "my.neg.float.field": -101.2,
            "my.true.bool.field": True,
            "my.false.bool.field": False,
        },
        "measurements": {
            "num_of_spans": {"value": 50.0},
            "eap.measurement": {"value": random.choice([1, 100, 1000])},
            **measurements,
        },
        "organization_id": 1,
        "origin": "auto.http.django",
        "project_id": 1,
        "received": 1721319572.877828,
        "retention_days": 90,
        "segment_id": "8873a98879faf06d",
        "sentry_tags": {
            "category": "http",
            "environment": "development",
            "op": "http.server",
            "platform": "python",
            "release": _RELEASE_TAG,
            "sdk.name": "sentry.python.django",
            "sdk.version": "2.7.0",
            "status": "ok",
            "status_code": "200",
            "thread.id": "8522009600",
            "thread.name": "uWSGIWorker1Core0",
            "trace.status": "ok",
            "transaction": "/api/0/relays/projectconfigs/",
            "transaction.method": "POST",
            "transaction.op": "http.server",
            "user": "ip:127.0.0.1",
        },
        "span_id": "123456781234567D",
        "tags": {
            "http.status_code": "200",
            "relay_endpoint_version": "3",
            "relay_id": "88888888-4444-4444-8444-cccccccccccc",
            "relay_no_cache": "False",
            "relay_protocol_version": "3",
            "relay_use_post_or_schedule": "True",
            "relay_use_post_or_schedule_rejected": "version",
            "server_name": _SERVER_NAME,
            "spans_over_limit": "False",
            "color": random.choice(["red", "green", "blue"]),
            "location": random.choice(["mobile", "frontend", "backend"]),
            **tags,
        },
        "trace_id": uuid.uuid4().hex,
        "start_timestamp_ms": int(dt.timestamp()) * 1000 - int(random.gauss(1000, 200)),
        "start_timestamp_precise": dt.timestamp(),
        "end_timestamp_precise": dt.timestamp() + 1,
    }


BASE_TIME = datetime.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
    minutes=180
)


@pytest.fixture(autouse=True)
def setup_teardown(clickhouse_db: None, redis_db: None) -> None:
    spans_storage = get_storage(StorageKey("eap_spans"))
    start = BASE_TIME
    messages = [gen_message(start - timedelta(minutes=i)) for i in range(120)]
    write_raw_unprocessed_events(spans_storage, messages)  # type: ignore


@pytest.mark.clickhouse_db
@pytest.mark.redis_db
class TestTraceItemTable(BaseApiTest):
    def test_basic(self) -> None:
        ts = Timestamp()
        ts.GetCurrentTime()
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=ts,
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                exists_filter=ExistsFilter(
                    key=AttributeKey(type=AttributeKey.TYPE_STRING, name="color")
                )
            ),
            columns=[
                Column(key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location"))
            ],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location")
                    )
                )
            ],
            limit=10,
        )
        response = self.app.post(
            "/rpc/EndpointTraceItemTable/v1", data=message.SerializeToString()
        )
        error_proto = ErrorProto()
        if response.status_code != 200:
            error_proto.ParseFromString(response.data)
        assert response.status_code == 200, error_proto

    def test_with_data(self, setup_teardown: Any) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                exists_filter=ExistsFilter(
                    key=AttributeKey(type=AttributeKey.TYPE_STRING, name="color")
                )
            ),
            columns=[
                Column(
                    key=AttributeKey(type=AttributeKey.TYPE_STRING, name="server_name")
                )
            ],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        key=AttributeKey(
                            type=AttributeKey.TYPE_STRING, name="server_name"
                        )
                    )
                )
            ],
        )
        response = EndpointTraceItemTable().execute(message)

        expected_response = TraceItemTableResponse(
            column_values=[
                TraceItemColumnValues(
                    attribute_name="server_name",
                    results=[AttributeValue(val_str=_SERVER_NAME) for _ in range(60)],
                )
            ],
            page_token=PageToken(offset=60),
        )
        assert response == expected_response

    def test_booleans_and_number_compares(self, setup_teardown: Any) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                or_filter=OrFilter(
                    filters=[
                        TraceItemFilter(
                            comparison_filter=ComparisonFilter(
                                key=AttributeKey(
                                    type=AttributeKey.TYPE_STRING,
                                    name="eap.measurement",
                                ),
                                op=ComparisonFilter.OP_LESS_THAN_OR_EQUALS,
                                value=AttributeValue(val_float=101),
                            ),
                        ),
                        TraceItemFilter(
                            comparison_filter=ComparisonFilter(
                                key=AttributeKey(
                                    type=AttributeKey.TYPE_STRING,
                                    name="eap.measurement",
                                ),
                                op=ComparisonFilter.OP_GREATER_THAN,
                                value=AttributeValue(val_float=999),
                            ),
                        ),
                    ]
                )
            ),
            columns=[
                Column(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_BOOLEAN, name="sentry.is_segment"
                    )
                ),
                Column(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_STRING, name="sentry.span_id"
                    )
                ),
            ],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        key=AttributeKey(
                            type=AttributeKey.TYPE_STRING, name="sentry.span_id"
                        )
                    )
                )
            ],
            limit=61,
        )
        response = EndpointTraceItemTable().execute(message)
        expected_response = TraceItemTableResponse(
            column_values=[
                TraceItemColumnValues(
                    attribute_name="sentry.is_segment",
                    results=[AttributeValue(val_bool=True) for _ in range(60)],
                ),
                TraceItemColumnValues(
                    attribute_name="sentry.span_id",
                    results=[
                        AttributeValue(val_str="123456781234567d") for _ in range(60)
                    ],
                ),
            ],
            page_token=PageToken(offset=60),
        )
        assert response == expected_response

    def test_with_virtual_columns(self, setup_teardown: Any) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        limit = 5
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                exists_filter=ExistsFilter(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_STRING, name="sentry.category"
                    )
                )
            ),
            columns=(
                [
                    Column(
                        key=AttributeKey(
                            type=AttributeKey.TYPE_STRING, name="sentry.project_name"
                        )
                    ),
                    Column(
                        key=AttributeKey(
                            type=AttributeKey.TYPE_STRING, name="sentry.release_version"
                        )
                    ),
                    Column(
                        key=AttributeKey(
                            type=AttributeKey.TYPE_STRING, name="sentry.sdk.name"
                        )
                    ),
                ]
            ),
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        key=AttributeKey(
                            type=AttributeKey.TYPE_STRING, name="sentry.project_name"
                        )
                    ),
                )
            ],
            limit=limit,
            virtual_column_contexts=[
                VirtualColumnContext(
                    from_column_name="sentry.project_id",
                    to_column_name="sentry.project_name",
                    value_map={"1": "sentry", "2": "snuba"},
                ),
                VirtualColumnContext(
                    from_column_name="sentry.release",
                    to_column_name="sentry.release_version",
                    value_map={_RELEASE_TAG: "4.2.0.69"},
                ),
            ],
        )
        response = EndpointTraceItemTable().execute(message)
        expected_response = TraceItemTableResponse(
            column_values=[
                TraceItemColumnValues(
                    attribute_name="sentry.project_name",
                    results=[AttributeValue(val_str="sentry") for _ in range(limit)],
                ),
                TraceItemColumnValues(
                    attribute_name="sentry.release_version",
                    results=[AttributeValue(val_str="4.2.0.69") for _ in range(limit)],
                ),
                TraceItemColumnValues(
                    attribute_name="sentry.sdk.name",
                    results=[
                        AttributeValue(val_str="sentry.python.django")
                        for _ in range(limit)
                    ],
                ),
            ],
            page_token=PageToken(offset=limit),
        )
        assert response.page_token == expected_response.page_token
        # make sure columns are ordered in the order they are requested
        assert [c.attribute_name for c in response.column_values] == [
            "sentry.project_name",
            "sentry.release_version",
            "sentry.sdk.name",
        ]
        assert response == expected_response, (
            MessageToDict(response),
            MessageToDict(expected_response),
        )

    def test_order_by_virtual_columns(self, setup_teardown: Any) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                exists_filter=ExistsFilter(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_STRING, name="sentry.category"
                    )
                )
            ),
            columns=[
                Column(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_STRING, name="special_color"
                    )
                ),
            ],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        key=AttributeKey(
                            type=AttributeKey.TYPE_STRING, name="special_color"
                        )
                    )
                )
            ],
            limit=61,
            virtual_column_contexts=[
                VirtualColumnContext(
                    from_column_name="color",
                    to_column_name="special_color",
                    value_map={"red": "1", "green": "2", "blue": "3"},
                ),
            ],
        )
        response = EndpointTraceItemTable().execute(message)
        result_colors = [c.val_str for c in response.column_values[0].results]
        assert sorted(result_colors) == result_colors

    def test_table_with_aggregates(self, setup_teardown: Any) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                exists_filter=ExistsFilter(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_STRING, name="sentry.category"
                    )
                )
            ),
            columns=[
                Column(
                    key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location")
                ),
                Column(
                    aggregation=AttributeAggregation(
                        aggregate=Function.FUNCTION_MAX,
                        key=AttributeKey(
                            type=AttributeKey.TYPE_FLOAT, name="my.float.field"
                        ),
                        label="max(my.float.field)",
                    ),
                ),
                Column(
                    aggregation=AttributeAggregation(
                        aggregate=Function.FUNCTION_AVG,
                        key=AttributeKey(
                            type=AttributeKey.TYPE_FLOAT, name="my.float.field"
                        ),
                        label="avg(my.float.field)",
                    ),
                ),
            ],
            group_by=[AttributeKey(type=AttributeKey.TYPE_STRING, name="location")],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location")
                    )
                ),
            ],
            limit=5,
        )
        response = EndpointTraceItemTable().execute(message)
        assert response.column_values == [
            TraceItemColumnValues(
                attribute_name="location",
                results=[
                    AttributeValue(val_str="backend"),
                    AttributeValue(val_str="frontend"),
                    AttributeValue(val_str="mobile"),
                ],
            ),
            TraceItemColumnValues(
                attribute_name="max(my.float.field)",
                results=[
                    AttributeValue(val_float=101.2),
                    AttributeValue(val_float=101.2),
                    AttributeValue(val_float=101.2),
                ],
            ),
            TraceItemColumnValues(
                attribute_name="avg(my.float.field)",
                results=[
                    AttributeValue(val_float=101.2),
                    AttributeValue(val_float=101.2),
                    AttributeValue(val_float=101.2),
                ],
            ),
        ]

    def test_table_with_columns_not_in_groupby(self, setup_teardown: Any) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            columns=[
                Column(
                    key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location")
                ),
                Column(
                    aggregation=AttributeAggregation(
                        aggregate=Function.FUNCTION_MAX,
                        key=AttributeKey(
                            type=AttributeKey.TYPE_FLOAT, name="my.float.field"
                        ),
                        label="max(my.float.field)",
                    )
                ),
            ],
            group_by=[],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location")
                    )
                ),
            ],
            limit=5,
        )
        with pytest.raises(BadSnubaRPCRequestException):
            EndpointTraceItemTable().execute(message)

    def test_order_by_non_selected(self) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                exists_filter=ExistsFilter(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_STRING, name="sentry.category"
                    )
                )
            ),
            columns=[
                Column(
                    key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location")
                ),
                Column(
                    aggregation=AttributeAggregation(
                        aggregate=Function.FUNCTION_AVG,
                        key=AttributeKey(
                            type=AttributeKey.TYPE_FLOAT, name="eap.measurement"
                        ),
                        label="avg(eap.measurment)",
                    )
                ),
            ],
            group_by=[AttributeKey(type=AttributeKey.TYPE_STRING, name="location")],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        aggregation=AttributeAggregation(
                            aggregate=Function.FUNCTION_MAX,
                            key=AttributeKey(
                                type=AttributeKey.TYPE_FLOAT, name="my.float.field"
                            ),
                            label="max(my.float.field)",
                        )
                    )
                ),
            ],
            limit=5,
        )
        with pytest.raises(BadSnubaRPCRequestException):
            EndpointTraceItemTable().execute(message)

    def test_order_by_aggregation(self) -> None:
        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1, 2, 3],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            filter=TraceItemFilter(
                exists_filter=ExistsFilter(
                    key=AttributeKey(
                        type=AttributeKey.TYPE_STRING, name="sentry.category"
                    )
                )
            ),
            columns=[
                Column(
                    key=AttributeKey(type=AttributeKey.TYPE_STRING, name="location")
                ),
                Column(
                    aggregation=AttributeAggregation(
                        aggregate=Function.FUNCTION_AVG,
                        key=AttributeKey(
                            type=AttributeKey.TYPE_FLOAT, name="eap.measurement"
                        ),
                        label="avg(eap.measurment)",
                    )
                ),
            ],
            group_by=[AttributeKey(type=AttributeKey.TYPE_STRING, name="location")],
            order_by=[
                TraceItemTableRequest.OrderBy(
                    column=Column(
                        aggregation=AttributeAggregation(
                            aggregate=Function.FUNCTION_AVG,
                            key=AttributeKey(
                                type=AttributeKey.TYPE_FLOAT, name="eap.measurement"
                            ),
                            label="avg(eap.measurment)",
                        )
                    )
                ),
            ],
            limit=5,
        )
        response = EndpointTraceItemTable().execute(message)
        measurements = [v.val_float for v in response.column_values[1].results]
        assert sorted(measurements) == measurements

    def test_aggregation_on_attribute_column(self) -> None:
        spans_storage = get_storage(StorageKey("eap_spans"))
        start = BASE_TIME
        measurement_val = 420.0
        measurement = {"custom_measurement": {"value": measurement_val}}
        tags = {"custom_tag": "blah"}
        messages_w_measurement = [
            gen_message(
                start - timedelta(minutes=i), measurements=measurement, tags=tags
            )
            for i in range(120)
        ]
        messages_no_measurement = [
            gen_message(start - timedelta(minutes=i), tags=tags) for i in range(120)
        ]
        write_raw_unprocessed_events(spans_storage, messages_w_measurement + messages_no_measurement)  # type: ignore

        ts = Timestamp(seconds=int(BASE_TIME.timestamp()))
        hour_ago = int((BASE_TIME - timedelta(hours=1)).timestamp())
        message = TraceItemTableRequest(
            meta=RequestMeta(
                project_ids=[1],
                organization_id=1,
                cogs_category="something",
                referrer="something",
                start_timestamp=Timestamp(seconds=hour_ago),
                end_timestamp=ts,
            ),
            columns=[
                Column(
                    aggregation=AttributeAggregation(
                        aggregate=Function.FUNCTION_AVG,
                        key=AttributeKey(
                            type=AttributeKey.TYPE_FLOAT, name="custom_measurement"
                        ),
                        label="avg(custom_measurement)",
                    )
                ),
            ],
            order_by=[],
            limit=5,
        )
        response = EndpointTraceItemTable().execute(message)
        measurement_avg = [v.val_float for v in response.column_values[0].results][0]
        assert measurement_avg == 420
