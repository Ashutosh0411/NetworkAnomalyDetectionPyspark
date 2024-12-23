import os
import sys
from flask import Flask, request, jsonify, render_template
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, udf
from pyspark.sql.types import DoubleType
from pyspark.ml.feature import VectorAssembler
from pyspark.ml.linalg import Vectors
import math
import pickle

# Ensure the correct Python executable is used
os.environ['PYSPARK_PYTHON'] = sys.executable
os.environ['PYSPARK_DRIVER_PYTHON'] = sys.executable

# Initialize Flask app
app = Flask(__name__)

# Initialize Spark session with more executors and memory
spark = SparkSession.builder \
    .appName("AnomalyDetectionApp") \
    .config("spark.executor.instances", "4") \
    .config("spark.executor.memory", "4g") \
    .config("spark.driver.memory", "4g") \
    .getOrCreate()

# Load the saved model
with open("anomaly_detection_model.pkl", "rb") as f:
    loaded_model = pickle.load(f)

proto_indexer_labels = loaded_model["proto_indexer_labels"]
state_indexer_labels = loaded_model["state_indexer_labels"]

# Load and preprocess data
def load_and_preprocess_data(file_path):
    data = spark.read.csv(file_path, header=True, inferSchema=True)

    # Create dictionaries for faster lookups
    proto_dict = {label: float(i) for i, label in enumerate(proto_indexer_labels)}
    state_dict = {label: float(i) for i, label in enumerate(state_indexer_labels)}

    # Use the loaded labels to transform Proto and State columns
    proto_udf = udf(lambda x: proto_dict.get(x, -1.0), DoubleType())
    state_udf = udf(lambda x: state_dict.get(x, -1.0), DoubleType())

    data = data.withColumn("Proto_Index", proto_udf(col("Proto")))
    data = data.withColumn("State_Index", state_udf(col("State")))

    feature_cols = ["Dur", "Proto_Index", "SrcBytes", "TotPkts", "TotBytes", "State_Index"]
    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features")
    data = assembler.transform(data)

    return data.select("features", "IsAnomaly")

# Optimized fuzzy_knn function
def fuzzy_knn(query_point, k=5, m=2):
    def calculate_distance(v1, v2):
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(v1, v2)))

    distances = [(calculate_distance(row['features'], query_point), row['IsAnomaly']) for row in data_collected]
    nearest_neighbors = sorted(distances, key=lambda x: x[0])[:k]

    total_distance = sum(1 / (d ** m) if d != 0 else float('inf') for d, _ in nearest_neighbors)

    if total_distance == float('inf'):
        total_distance = 1

    anomaly_score = sum(
        ((1 / (d ** m)) / total_distance) * is_anomaly if d != 0 else is_anomaly
        for d, is_anomaly in nearest_neighbors
    )

    return anomaly_score

# Load data and preprocess it
data = load_and_preprocess_data("Anomoly.csv")

# Cache the preprocessed data
data.cache()

# Collect the data to driver
data_collected = data.collect()

@app.route('/')
def home():
    return render_template('predict_form.html')

@app.route('/predict', methods=['POST'])
def predict():
    try:
        # Extract and validate input features
        try:
            Dur = float(request.form['duration'])
            Proto = request.form['protocol']
            SrcBytes = float(request.form['srcbytes'])
            TotPkts = float(request.form['totpkts'])
            TotBytes = float(request.form['totbytes'])
            State = request.form['state']
        except (KeyError, ValueError):
            return jsonify(error="Invalid input data"), 400

        # Convert protocol and state to their corresponding indices
        try:
            Proto_Index = float(proto_indexer_labels.index(Proto))
            State_Index = float(state_indexer_labels.index(State))
        except ValueError:
            return jsonify(error="Invalid protocol or state"), 400

        # Prepare input features as a vector
        query_point = Vectors.dense([Dur, Proto_Index, SrcBytes, TotPkts, TotBytes, State_Index])

        # Use fuzzy KNN to make prediction
        anomaly_score = fuzzy_knn(query_point)
        is_anomaly = 1 if anomaly_score > 0.5 else 0

        # Return the result as JSON
        return jsonify(result=float(is_anomaly))

    except Exception as e:
        return jsonify(error=f"An internal error occurred: {str(e)}"), 500

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
