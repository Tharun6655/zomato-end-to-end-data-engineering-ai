import os
import json
import ast
import snowflake.connector
from openai import OpenAI
from dotenv import load_dotenv


# ---------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------
load_dotenv()


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
MODEL = "openrouter/free"

SAMPLE_N = 20

TOPICS = [
    "food quality",
    "delivery",
    "pricing",
    "service",
    "packaging",
    "other",
]


# ---------------------------------------------------------
# OpenRouter client
# ---------------------------------------------------------
client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)


# ---------------------------------------------------------
# Prompt
# ---------------------------------------------------------
SYSTEM_PROMPT = f"""
You classify customer reviews for a food delivery application.

For the review provided, return:

- sentiment_label: positive, negative, or neutral
- sentiment_score: a number between -1.0 and 1.0
- topic: exactly one of {TOPICS}
- key_issue: a short phrase of 6 words or less describing the
  main issue in the review. If there is no issue, return null.

Return ONLY valid JSON.

Use this exact structure:

{{
    "sentiment_label": "positive",
    "sentiment_score": 0.8,
    "topic": "delivery",
    "key_issue": null
}}
"""


# ---------------------------------------------------------
# Snowflake connection
# ---------------------------------------------------------
def get_connection():
    required_vars = [
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    ]

    missing = [
        var
        for var in required_vars
        if not os.getenv(var)
    ]

    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}"
        )

    return snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
    )


# ---------------------------------------------------------
# Validate OpenRouter API key
# ---------------------------------------------------------
def validate_api_key():
    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set in the .env file."
        )


# ---------------------------------------------------------
# Create AI output table
# ---------------------------------------------------------
def create_output_table(cursor):
    cursor.execute(
        "CREATE SCHEMA IF NOT EXISTS ZOMATO.AI"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ZOMATO.AI.REVIEW_ENRICHED (
            REVIEW_ID STRING,
            SENTIMENT_LABEL STRING,
            SENTIMENT_SCORE FLOAT,
            TOPIC STRING,
            KEY_ISSUE STRING,
            MODEL STRING,
            ENRICHED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
        )
        """
    )


# ---------------------------------------------------------
# Get reviews that haven't been enriched
# ---------------------------------------------------------
def get_reviews_to_enrich(cursor):
    cursor.execute(
        f"""
        SELECT
            REVIEW_ID,
            COMMENT
        FROM ZOMATO.RAW.REVIEWS
        WHERE REVIEW_ID NOT IN (
            SELECT REVIEW_ID
            FROM ZOMATO.AI.REVIEW_ENRICHED
        )
        AND COMMENT IS NOT NULL
        LIMIT {SAMPLE_N}
        """
    )

    return cursor.fetchall()


# ---------------------------------------------------------
# Classify one review using OpenRouter
# ---------------------------------------------------------
def classify_review(comment):
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": str(comment),
            },
        ],
    )

    answer = response.choices[0].message.content

    if not answer:
        raise ValueError("OpenRouter returned an empty response.")

    answer = answer.strip()

    if answer.startswith("```json"):
        answer = answer[7:]

    if answer.startswith("```"):
        answer = answer[3:]

    if answer.endswith("```"):
        answer = answer[:-3]

    answer = answer.strip()

    try:
        return json.loads(answer)
    except json.JSONDecodeError:
        import ast
        return ast.literal_eval(answer)

# ---------------------------------------------------------
# Validate AI result
# ---------------------------------------------------------
def validate_labels(labels):
    required_fields = [
        "sentiment_label",
        "sentiment_score",
        "topic",
        "key_issue",
    ]

    for field in required_fields:
        if field not in labels:
            raise ValueError(
                f"Missing field in AI response: {field}"
            )

    sentiment = str(labels["sentiment_label"]).lower()

    if sentiment not in {
        "positive",
        "negative",
        "neutral",
    }:
        raise ValueError(
            f"Invalid sentiment_label: {sentiment}"
        )

    try:
        score = float(labels["sentiment_score"])
    except (TypeError, ValueError):
        raise ValueError(
            "sentiment_score must be numeric."
        )

    if not -1.0 <= score <= 1.0:
        raise ValueError(
            f"sentiment_score must be between -1 and 1. Got {score}"
        )

    topic = str(labels["topic"]).lower()

    if topic not in TOPICS:
        raise ValueError(
            f"Invalid topic: {topic}"
        )

    return {
        "sentiment_label": sentiment,
        "sentiment_score": score,
        "topic": topic,
        "key_issue": labels["key_issue"],
    }


# ---------------------------------------------------------
# Save results to Snowflake
# ---------------------------------------------------------
def save_results(cursor, results):
    if not results:
        print("No enriched reviews to save.")
        return

    print(
        f"Saving {len(results)} enriched reviews to Snowflake..."
    )

    cursor.executemany(
        """
        INSERT INTO ZOMATO.AI.REVIEW_ENRICHED
        (
            REVIEW_ID,
            SENTIMENT_LABEL,
            SENTIMENT_SCORE,
            TOPIC,
            KEY_ISSUE,
            MODEL
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        results,
    )


# ---------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------
def main():

    validate_api_key()

    conn = None
    cursor = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        print("Connected to Snowflake.")

        create_output_table(cursor)

        reviews = get_reviews_to_enrich(cursor)

        if not reviews:
            print("No new reviews to enrich.")
            return

        print(
            f"Enriching {len(reviews)} reviews..."
        )

        results = []

        for review_id, comment in reviews:

            print(
                f"Classifying review {review_id}: {comment}"
            )

            try:

                labels = classify_review(comment)

                labels = validate_labels(labels)

                print(
                    f"Labels for review {review_id}: {labels}"
                )

                results.append(
                    (
                        review_id,
                        labels["sentiment_label"],
                        labels["sentiment_score"],
                        labels["topic"],
                        labels["key_issue"],
                        MODEL,
                    )
                )

            except Exception as e:

                print(
                    f"Error occurred while classifying "
                    f"review {review_id}: {e}"
                )

        save_results(cursor, results)

        conn.commit()

        print(
            f"Saved {len(results)} enriched reviews to Snowflake."
        )

    except Exception as e:

        if conn:
            conn.rollback()

        print(f"Pipeline failed: {e}")

        raise

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------
if __name__ == "__main__":
    main()