select
    customer_id,
    customer_name,
    email,
    age,
    gender,
    marital_status,
    occupation,
    income_band,
    education,
    family_size
from {{ ref('stg_users') }}