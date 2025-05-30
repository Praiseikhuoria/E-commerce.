from unittest import mock
from unittest.mock import ANY, patch

import pytest
from django.test import override_settings

from .....checkout.actions import call_checkout_info_event
from .....checkout.utils import invalidate_checkout
from .....core.models import EventDelivery
from .....product.models import ProductChannelListing, ProductVariantChannelListing
from .....webhook.event_types import WebhookEventAsyncType, WebhookEventSyncType
from ....core.utils import to_global_id_or_none
from ....tests.utils import assert_no_permission, get_graphql_content
from ..utils import assert_address_data

MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE = """
    mutation checkoutBillingAddressUpdate(
            $checkoutId: ID,
            $id: ID,
            $billingAddress: AddressInput!
            $saveAddress: Boolean
            $validationRules: CheckoutAddressValidationRules
        ) {
        checkoutBillingAddressUpdate(
                id: $id,
                checkoutId: $checkoutId,
                billingAddress: $billingAddress
                validationRules: $validationRules
                saveAddress: $saveAddress
        ){
            checkout {
                token,
                id
            },
            errors {
                field
                code
                message
            }
        }
    }
"""


def test_checkout_billing_address_update_by_id(
    user_api_client, checkout_with_item, graphql_address_data
):
    checkout = checkout_with_item
    assert checkout.shipping_address is None

    query = MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE
    billing_address = graphql_address_data

    variables = {
        "id": to_global_id_or_none(checkout),
        "billingAddress": billing_address,
    }

    response = user_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    checkout.refresh_from_db()
    assert_address_data(checkout.billing_address, billing_address)
    assert checkout.save_billing_address is True


@pytest.mark.parametrize(
    ("channel_listing_model", "listing_filter_field"),
    [
        (ProductVariantChannelListing, "variant_id"),
        (ProductChannelListing, "product__variants__id"),
    ],
)
def test_checkout_billing_address_update_when_line_without_listing(
    channel_listing_model,
    listing_filter_field,
    user_api_client,
    checkout_with_item,
    graphql_address_data,
):
    checkout = checkout_with_item
    line_without_listing = checkout.lines.first()

    channel_listing_model.objects.filter(
        channel_id=checkout.channel_id,
        **{listing_filter_field: line_without_listing.variant_id},
    ).delete()

    assert checkout.shipping_address is None

    query = MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE
    billing_address = graphql_address_data

    variables = {
        "id": to_global_id_or_none(checkout),
        "billingAddress": billing_address,
    }

    response = user_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    checkout.refresh_from_db()
    assert_address_data(checkout.billing_address, billing_address)
    assert checkout.save_billing_address is True


def test_checkout_billing_address_update_by_id_without_required_fields(
    user_api_client, checkout_with_item, graphql_address_data
):
    checkout = checkout_with_item
    assert checkout.shipping_address is None

    query = MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE

    graphql_address_data["streetAddress1"] = ""
    graphql_address_data["streetAddress2"] = ""
    graphql_address_data["postalCode"] = ""

    billing_address = graphql_address_data

    variables = {
        "id": to_global_id_or_none(checkout_with_item),
        "billingAddress": billing_address,
    }

    response = user_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert data["errors"]
    assert data["errors"] == [
        {
            "code": "REQUIRED",
            "field": "postalCode",
            "message": "This field is required.",
        },
        {
            "code": "REQUIRED",
            "field": "streetAddress1",
            "message": "This field is required.",
        },
    ]


def test_checkout_billing_address_update_by_id_without_street_address_2(
    user_api_client, checkout_with_item, graphql_address_data
):
    checkout = checkout_with_item
    assert checkout.shipping_address is None

    query = MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE

    graphql_address_data["streetAddress2"] = ""

    billing_address = graphql_address_data

    variables = {
        "id": to_global_id_or_none(checkout_with_item),
        "billingAddress": billing_address,
    }

    response = user_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    checkout.refresh_from_db()
    assert_address_data(checkout.billing_address, billing_address)
    assert checkout.save_billing_address is True


@mock.patch(
    "saleor.graphql.checkout.mutations.checkout_billing_address_update."
    "invalidate_checkout",
    wraps=invalidate_checkout,
)
def test_checkout_billing_address_update(
    mocked_invalidate_checkout,
    user_api_client,
    checkout_with_item,
    graphql_address_data,
):
    checkout = checkout_with_item
    assert checkout.shipping_address is None
    previous_last_change = checkout.last_change

    query = """
    mutation checkoutBillingAddressUpdate(
            $id: ID, $billingAddress: AddressInput!) {
        checkoutBillingAddressUpdate(
                id: $id, billingAddress: $billingAddress) {
            checkout {
                token,
                id
            },
            errors {
                field,
                message
            }
        }
    }
    """
    billing_address = graphql_address_data
    variables = {
        "id": to_global_id_or_none(checkout_with_item),
        "billingAddress": billing_address,
    }

    response = user_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    checkout.refresh_from_db()
    assert_address_data(checkout.billing_address, billing_address)
    assert checkout.last_change != previous_last_change
    assert mocked_invalidate_checkout.call_count == 1
    assert checkout.save_billing_address is True


@pytest.mark.parametrize(
    "address_data",
    [
        {"country": "PL"},  # missing postalCode, streetAddress
        {"country": "PL", "postalCode": "53-601"},  # missing streetAddress
        {"country": "US"},
        {
            "country": "US",
            "city": "New York",
        },  # missing postalCode, streetAddress, countryArea
    ],
)
def test_checkout_billing_address_update_with_skip_required_doesnt_raise_error(
    address_data, checkout_with_items, user_api_client
):
    # given
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": address_data,
        "validationRules": {"checkRequiredFields": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    assert checkout_with_items.billing_address


def test_checkout_billing_address_update_with_skip_required_overwrite_address(
    checkout_with_items, user_api_client, address
):
    # given
    checkout_with_items.billing_address = address
    checkout_with_items.save()

    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": {
            "postalCode": "",
            "city": "",
            "country": "US",
        },
        "validationRules": {"checkRequiredFields": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]

    assert checkout_with_items.billing_address.city == ""
    assert checkout_with_items.billing_address.postal_code == ""


def test_checkout_billing_address_update_with_skip_required_raises_validation_error(
    checkout_with_items, user_api_client
):
    # given
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": {"country": "US", "postalCode": "XX-123"},
        "validationRules": {"checkRequiredFields": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert len(data["errors"]) == 1
    assert data["errors"][0]["code"] == "INVALID"
    assert data["errors"][0]["field"] == "postalCode"
    assert not checkout_with_items.billing_address


def test_checkout_billing_address_update_with_skip_required_saves_address(
    checkout_with_items, user_api_client
):
    # given
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": {"country": "PL", "postalCode": "53-601"},
        "validationRules": {"checkRequiredFields": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]

    assert checkout_with_items.billing_address
    assert checkout_with_items.billing_address.country.code == "PL"
    assert checkout_with_items.billing_address.postal_code == "53-601"


@pytest.mark.parametrize(
    "address_data",
    [
        {
            "country": "PL",
            "city": "Wroclaw",
            "postalCode": "XYZ",
            "streetAddress1": "Teczowa 7",
        },  # incorrect postalCode
        {
            "country": "US",
            "city": "New York",
            "countryArea": "ABC",
            "streetAddress1": "New street",
            "postalCode": "53-601",
        },  # incorrect postalCode
    ],
)
def test_checkout_billing_address_update_with_skip_value_check_doesnt_raise_error(
    address_data, checkout_with_items, user_api_client
):
    # given
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": address_data,
        "validationRules": {"checkFieldsFormat": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    assert checkout_with_items.billing_address


@pytest.mark.parametrize(
    "address_data",
    [
        {
            "country": "PL",
            "city": "Wroclaw",
            "postalCode": "XYZ",
        },  # incorrect postalCode
        {
            "country": "US",
            "city": "New York",
            "countryArea": "XYZ",
            "postalCode": "XYZ",
        },  # incorrect postalCode
    ],
)
def test_checkout_billing_address_update_with_skip_value_raises_required_fields_error(
    address_data, checkout_with_items, user_api_client
):
    # given
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": address_data,
        "validationRules": {"checkFieldsFormat": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert len(data["errors"]) == 1
    assert data["errors"][0]["code"] == "REQUIRED"
    assert data["errors"][0]["field"] == "streetAddress1"
    assert not checkout_with_items.billing_address


def test_checkout_billing_address_update_with_skip_value_check_saves_address(
    checkout_with_items, user_api_client
):
    # given
    city = "Wroclaw"
    street_address = "Teczowa 7"
    postal_code = "XX-601"  # incorrect format for PL
    country_code = "PL"
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": {
            "country": country_code,
            "city": city,
            "streetAddress1": street_address,
            "postalCode": postal_code,
        },
        "validationRules": {"checkFieldsFormat": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]

    address = checkout_with_items.billing_address
    assert address

    assert address.street_address_1 == street_address
    assert address.city == city
    assert address.country.code == country_code
    assert address.postal_code == postal_code


@pytest.mark.parametrize(
    "address_data",
    [
        {
            "country": "PL",
            "postalCode": "XYZ",
        },  # incorrect postalCode, missing city, streetAddress
        {
            "country": "US",
            "countryArea": "DC",
            "postalCode": "XYZ",
        },  # incorrect postalCode, missing city
    ],
)
def test_checkout_billing_address_update_with_skip_value_and_skip_required_fields(
    address_data, checkout_with_items, user_api_client
):
    # given
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": address_data,
        "validationRules": {"checkFieldsFormat": False, "checkRequiredFields": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()
    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    assert checkout_with_items.billing_address


def test_checkout_address_update_with_skip_value_and_skip_required_saves_address(
    checkout_with_items, user_api_client
):
    # given
    city = "Wroclaw"
    postal_code = "XX-601"  # incorrect format for PL
    country_code = "PL"

    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": {
            "country": country_code,
            "city": city,
            "postalCode": postal_code,
        },
        "validationRules": {"checkFieldsFormat": False, "checkRequiredFields": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    address = checkout_with_items.billing_address
    assert not data["errors"]

    assert address
    assert address.country.code == country_code
    assert address.postal_code == postal_code
    assert address.city == city
    assert address.street_address_1 == ""


def test_checkout_billing_address_update_with_disabled_fields_normalization(
    checkout_with_items, user_api_client
):
    # given
    address_data = {
        "country": "US",
        "city": "Washington",
        "countryArea": "District of Columbia",
        "streetAddress1": "1600 Pennsylvania Avenue NW",
        "postalCode": "20500",
    }
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": address_data,
        "validationRules": {"enableFieldsNormalization": False},
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    checkout_with_items.refresh_from_db()

    content = get_graphql_content(response)
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    assert checkout_with_items
    billing_address = checkout_with_items.billing_address
    assert billing_address
    assert billing_address.city == address_data["city"]
    assert billing_address.country_area == address_data["countryArea"]
    assert billing_address.postal_code == address_data["postalCode"]
    assert billing_address.street_address_1 == address_data["streetAddress1"]


def test_with_active_problems_flow(
    api_client,
    checkout_with_problems,
    graphql_address_data,
):
    # given
    channel = checkout_with_problems.channel
    channel.use_legacy_error_flow_for_checkout = False
    channel.save(update_fields=["use_legacy_error_flow_for_checkout"])

    new_address = graphql_address_data
    variables = {
        "id": to_global_id_or_none(checkout_with_problems),
        "billingAddress": new_address,
    }

    # when
    response = api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )
    content = get_graphql_content(response)

    # then
    assert not content["data"]["checkoutBillingAddressUpdate"]["errors"]


def test_checkout_billing_address_skip_validation_by_customer(
    checkout_with_items, user_api_client, graphql_address_data_skipped_validation
):
    # given
    address_data = graphql_address_data_skipped_validation
    invalid_postal_code = "invalid_postal_code"
    address_data["postalCode"] = invalid_postal_code

    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": address_data,
    }

    # when
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE, variables
    )

    # then
    assert_no_permission(response)


def test_checkout_billing_address_skip_validation_by_app(
    checkout_with_items,
    app_api_client,
    graphql_address_data_skipped_validation,
    permission_handle_checkouts,
):
    # given
    checkout = checkout_with_items
    address_data = graphql_address_data_skipped_validation
    invalid_postal_code = "invalid_postal_code"
    address_data["postalCode"] = invalid_postal_code

    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": address_data,
    }

    # when
    response = app_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE,
        variables,
        permissions=[permission_handle_checkouts],
    )
    content = get_graphql_content(response)

    # then
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    checkout.refresh_from_db()
    assert checkout.billing_address.postal_code == invalid_postal_code
    assert checkout.billing_address.validation_skipped is True


@patch(
    "saleor.graphql.checkout.mutations.checkout_billing_address_update.call_checkout_info_event",
    wraps=call_checkout_info_event,
)
@patch("saleor.webhook.transport.synchronous.transport.send_webhook_request_sync")
@patch(
    "saleor.webhook.transport.asynchronous.transport.send_webhook_request_async.apply_async"
)
@override_settings(PLUGINS=["saleor.plugins.webhook.plugin.WebhookPlugin"])
def test_checkout_billing_address_triggers_webhooks(
    mocked_send_webhook_request_async,
    mocked_send_webhook_request_sync,
    wrapped_call_checkout_info_event,
    setup_checkout_webhooks,
    settings,
    user_api_client,
    checkout_with_item,
    graphql_address_data,
    address,
):
    # given
    mocked_send_webhook_request_sync.return_value = []
    (
        tax_webhook,
        shipping_webhook,
        shipping_filter_webhook,
        checkout_updated_webhook,
    ) = setup_checkout_webhooks(WebhookEventAsyncType.CHECKOUT_UPDATED)

    checkout = checkout_with_item

    # Ensure shipping is set so shipping webhooks are emitted
    checkout.shipping_address = address
    checkout.billing_address = address

    checkout.save()

    query = MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE
    billing_address = graphql_address_data

    variables = {
        "id": to_global_id_or_none(checkout),
        "billingAddress": billing_address,
    }

    # when
    response = user_api_client.post_graphql(query, variables)

    # then
    content = get_graphql_content(response)
    assert not content["data"]["checkoutBillingAddressUpdate"]["errors"]

    assert wrapped_call_checkout_info_event.called

    # confirm that event delivery was generated for each webhook.
    checkout_update_delivery = EventDelivery.objects.get(
        webhook_id=checkout_updated_webhook.id
    )
    mocked_send_webhook_request_async.assert_called_once_with(
        kwargs={
            "event_delivery_id": checkout_update_delivery.id,
            "telemetry_context": ANY,
        },
        queue=settings.CHECKOUT_WEBHOOK_EVENTS_CELERY_QUEUE_NAME,
        bind=True,
        retry_backoff=10,
        retry_kwargs={"max_retries": 5},
    )

    # confirm each sync webhook was called without saving event delivery
    assert mocked_send_webhook_request_sync.call_count == 3
    assert not EventDelivery.objects.exclude(
        webhook_id=checkout_updated_webhook.id
    ).exists()

    shipping_methods_call, filter_shipping_call, tax_delivery_call = (
        mocked_send_webhook_request_sync.mock_calls
    )
    shipping_methods_delivery = shipping_methods_call.args[0]
    assert shipping_methods_delivery.webhook_id == shipping_webhook.id
    assert (
        shipping_methods_delivery.event_type
        == WebhookEventSyncType.SHIPPING_LIST_METHODS_FOR_CHECKOUT
    )
    assert shipping_methods_call.kwargs["timeout"] == settings.WEBHOOK_SYNC_TIMEOUT

    filter_shipping_delivery = filter_shipping_call.args[0]
    assert filter_shipping_delivery.webhook_id == shipping_filter_webhook.id
    assert (
        filter_shipping_delivery.event_type
        == WebhookEventSyncType.CHECKOUT_FILTER_SHIPPING_METHODS
    )
    assert filter_shipping_call.kwargs["timeout"] == settings.WEBHOOK_SYNC_TIMEOUT

    tax_delivery = tax_delivery_call.args[0]
    assert tax_delivery.webhook_id == tax_webhook.id


def test_checkout_billing_address_update_reset_the_save_address_flag_to_default_value(
    checkout_with_items,
    user_api_client,
    graphql_address_data,
    address,
):
    checkout = checkout_with_items
    # given checkout billing and billing address set both save billing flags different
    # than default value - set to False
    checkout.billing_address = address
    checkout.billing_address = address
    checkout.save_billing_address = False
    checkout.save_shipping_address = False
    checkout.save(
        update_fields=[
            "billing_address",
            "billing_address",
            "save_billing_address",
            "save_shipping_address",
        ]
    )

    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": graphql_address_data,
    }

    # when the checkout billing address is updated without providing saveAddress flag
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE,
        variables,
    )
    content = get_graphql_content(response)

    # then the checkout billing address is updated, and `save_billing_address` is
    # reset to the default True value; the `save_billing_address` is not changed
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]

    checkout.refresh_from_db()
    assert_address_data(checkout.billing_address, graphql_address_data)
    assert checkout.save_billing_address is True
    assert checkout.save_shipping_address is False


def test_checkout_billing_address_update_with_save_address_to_false(
    checkout_with_items,
    user_api_client,
    graphql_address_data,
):
    # given checkout with default saving address values
    checkout = checkout_with_items

    save_address = False
    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": graphql_address_data,
        "saveAddress": save_address,
    }

    # when update billing address with saveAddress flag set to False
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE,
        variables,
    )
    content = get_graphql_content(response)

    # then the address should be saved and the save_billing_address should be False
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    checkout.refresh_from_db()
    assert_address_data(checkout.billing_address, graphql_address_data)
    assert checkout.save_billing_address is save_address
    assert checkout.save_shipping_address is True


def test_checkout_billing_address_update_change_save_address_option_to_true(
    checkout_with_items,
    user_api_client,
    graphql_address_data,
):
    # given checkout with save addresses settings to False
    checkout = checkout_with_items
    checkout.save_billing_address = False
    checkout.save_shipping_address = False
    checkout.save(update_fields=["save_billing_address", "save_shipping_address"])

    variables = {
        "id": to_global_id_or_none(checkout_with_items),
        "billingAddress": graphql_address_data,
        "saveAddress": True,
    }

    # when the billing address is updated with saveAddress flag set to True
    response = user_api_client.post_graphql(
        MUTATION_CHECKOUT_BILLING_ADDRESS_UPDATE,
        variables,
    )
    content = get_graphql_content(response)

    # then the address should be saved and the save_billing_address should be True
    # the save_billing_address should not be changed
    data = content["data"]["checkoutBillingAddressUpdate"]
    assert not data["errors"]
    checkout.refresh_from_db()
    assert_address_data(checkout.billing_address, graphql_address_data)
    assert checkout.save_billing_address is True
    assert checkout.save_shipping_address is False
