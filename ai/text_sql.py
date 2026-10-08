import os
import json
import pandas as pd
import streamlit as st
import snowflake.connector
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

CHAT_MODEL = "openrouter/free"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

FORBIDDEN_WORDS = [
    "drop",
    "delete",
    "truncate",
    "alter",
    "update",
    "insert",
    "create",
    "replace",
    "grant",
    "revoke",
]

EXAMPLE_QUESTIONS = [
    "Top 10 cities by GMV",
    "Which cuisine has the most orders?",
    "Average delivery time by city, worst first",
    "Cancel rate by payment method",
]

# ============================================================
# SCHEMA
# ============================================================

SCHEMA = """
Available Snowflake tables in the MARTS schema:

FCT_ORDERS(
    order_id,
    order_date,
    customer_id,
    restaurant_id,
    city,
    cuisine,
    payment_method,
    order_status,
    is_delivered,
    sales_amount,
    discount,
    delivery_fee,
    gst,
    customer_rating,
    delivery_time_min
)

DIM_RESTAURANTS(
    restaurant_id,
    restaurant_name,
    city,
    cuisine,
    rating,
    cost
)

DIM_CUSTOMER(
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
)

MART_DAILY_CITY_REVENUE(
    order_date,
    city,
    orders,
    cancel_rate,
    gmv,
    aov
)

MART_RESTAURANT_PERFORMANCE(
    restaurant_id,
    restaurant_name,
    city,
    cuisine,
    orders,
    revenue,
    avg_customer_rating,
    cancel_rate
)

MART_DELIVARY_SLA(
    city,
    order_hour,
    delivered_orders,
    p50_delivery_min,
    late_rate
)

Important:
- GMV means delivered revenue.
- Prefer MART_ tables when they directly answer the question.
- Use FCT_ORDERS for detailed order-level analysis.
- Use only the tables and columns listed above.
- Use bare table names.
- Do not use database or schema prefixes.
- Do not invent tables or columns.
"""

# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = f"""
You are a Snowflake Text-to-SQL expert.

Convert the user's natural-language question into ONE SQL query.

Rules:

1. Generate SELECT queries only.
2. WITH queries are allowed.
3. Never modify data.
4. Never use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE,
   TRUNCATE, GRANT, REVOKE, or REPLACE.
5. Use only the tables and columns provided in the schema.
6. Use bare table names.
7. Do not use ZOMATO.MARTS. prefixes.
8. Add LIMIT 100 or less for list/detail queries.
9. Do not add LIMIT for a single aggregate result.
10. Return ONLY valid JSON.

Return exactly:

{{
    "sql": "SELECT ..."
}}

{SCHEMA}
"""

# ============================================================
# OPENROUTER CLIENT
# ============================================================

def get_openrouter_client():

    if not OPENROUTER_API_KEY:
        return None

    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )


# ============================================================
# SNOWFLAKE CONNECTION
# ============================================================

@st.cache_resource
def get_connection():

    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database="ZOMATO",
        schema="MARTS",
        role="DBT_ROLE",
    )


# ============================================================
# GENERATE SQL
# ============================================================

def generate_sql(question):

    client = get_openrouter_client()

    if client is None:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not configured."
        )

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": question,
            },
        ],
    )

    answer = response.choices[0].message.content

    if not answer:
        raise ValueError(
            "OpenRouter returned an empty response."
        )

    answer = answer.strip()

    # Remove markdown code fences if the model returns them
    if answer.startswith("```json"):
        answer = answer[7:]

    elif answer.startswith("```"):
        answer = answer[3:]

    if answer.endswith("```"):
        answer = answer[:-3]

    answer = answer.strip()

    result = json.loads(answer)

    if "sql" not in result:
        raise ValueError(
            "OpenRouter response does not contain 'sql'."
        )

    sql = result["sql"]

    sql = sql.replace("ZOMATO.MARTS.", "")
    sql = sql.replace("ZOMATO.", "")

    return sql.strip().rstrip(";")


# ============================================================
# SQL SAFETY CHECK
# ============================================================

def is_safe(sql):

    normalized = " ".join(
        sql.lower().split()
    )

    if not (
        normalized.startswith("select")
        or normalized.startswith("with")
    ):
        return False

    # Reject multiple SQL statements
    if ";" in normalized:
        return False

    for word in FORBIDDEN_WORDS:

        if f" {word} " in f" {normalized} ":
            return False

    return True


# ============================================================
# RUN SNOWFLAKE QUERY
# ============================================================

def run_query(sql):

    conn = get_connection()

    cursor = conn.cursor()

    try:

        result = cursor.execute(
            sql
        ).fetch_pandas_all()

        return result

    finally:

        cursor.close()


# ============================================================
# STREAMLIT UI
# ============================================================

st.set_page_config(
    page_title="Zomato Text-to-SQL",
    page_icon="🍽️",
    layout="wide",
)

st.title("🍽️ Chat with your Zomato Data")

st.caption(
    f"Ask in English → {CHAT_MODEL} generates SQL → Snowflake executes it"
)

# ============================================================
# CHECK OPENROUTER
# ============================================================

if OPENROUTER_API_KEY:

    st.success(
        "OpenRouter API key loaded."
    )

else:

    st.error(
        "OPENROUTER_API_KEY is missing from your .env file."
    )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("Example Questions")

    for question_example in EXAMPLE_QUESTIONS:

        st.write(
            f"• {question_example}"
        )


# ============================================================
# USER QUESTION
# ============================================================

question = st.text_input(
    "Enter your question",
    placeholder="e.g. Top 10 restaurants by revenue in Bangalore",
)


# ============================================================
# PROCESS QUESTION
# ============================================================

if question:

    try:

        with st.spinner(
            "Generating SQL..."
        ):

            sql = generate_sql(
                question
            )

        st.subheader(
            "Generated SQL"
        )

        st.code(
            sql,
            language="sql"
        )

        # ----------------------------------------------------
        # SAFETY CHECK
        # ----------------------------------------------------

        if not is_safe(sql):

            st.error(
                "The generated SQL is not safe to execute."
            )

        else:

            with st.spinner(
                "Running query in Snowflake..."
            ):

                df = run_query(
                    sql
                )

            st.success(
                f"{len(df)} rows returned"
            )

            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
            )

            # ------------------------------------------------
            # SIMPLE CHART
            # ------------------------------------------------

            if (
                len(df.columns) == 2
                and pd.api.types.is_numeric_dtype(
                    df.iloc[:, 1]
                )
            ):

                st.subheader(
                    "Visualization"
                )

                st.bar_chart(
                    df,
                    x=df.columns[0],
                    y=df.columns[1],
                )

    except Exception as e:

        st.error(
            f"Error: {e}"
        )