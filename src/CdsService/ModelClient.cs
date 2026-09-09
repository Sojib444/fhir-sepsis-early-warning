using System.Net.Http.Json;
using System.Text.Json;

namespace CdsService;

/// <summary>
/// Calls the Python model service. Feature construction lives entirely on the
/// Python side (src/model_api), which runs the exact `features.window_features_frame`
/// code path used at training time — the CDS service never re-implements feature
/// math (§12.2, §12.3).
/// </summary>
public sealed class ModelClient
{
    private readonly HttpClient _http;

    public ModelClient(HttpClient http) => _http = http;

    /// <summary>Builds the feature row for the patient's current hour.</summary>
    public async Task<FeatureRow> BuildFeaturesAsync(PatientSnapshot snapshot)
    {
        var observations = snapshot.Observations
            .Select(o => new { o.Hour, o.Var, o.Value });
        var payload = new
        {
            patient_id = snapshot.PatientId,
            observations,
            snapshot.Age,
            snapshot.Gender,
            snapshot.Iculos,
        };

        HttpResponseMessage resp = await _http.PostAsJsonAsync("/features", payload);
        resp.EnsureSuccessStatusCode();

        using JsonDocument doc = await JsonDocument.ParseAsync(await resp.Content.ReadAsStreamAsync());
        JsonElement root = doc.RootElement;
        int hour = root.GetProperty("hour").GetInt32();
        Dictionary<string, double?> features = new(StringComparer.Ordinal);
        foreach (JsonProperty prop in root.GetProperty("features").EnumerateObject())
        {
            features[prop.Name] = prop.Value.ValueKind == JsonValueKind.Null ? null : prop.Value.GetDouble();
        }
        return new FeatureRow(features, hour);
    }

    /// <summary>Scores the feature row; risk, threshold, indicator and SHAP come back together.</summary>
    public async Task<ModelResult> ScoreAsync(FeatureRow row)
    {
        var payload = new { features = row.Features };
        HttpResponseMessage resp = await _http.PostAsJsonAsync("/predict", payload);
        resp.EnsureSuccessStatusCode();

        using JsonDocument doc = await JsonDocument.ParseAsync(await resp.Content.ReadAsStreamAsync());
        JsonElement root = doc.RootElement;
        double risk = root.GetProperty("risk").GetDouble();
        double threshold = root.GetProperty("threshold").GetDouble();
        string indicator = root.GetProperty("indicator").GetString()!;

        var topShap = new List<ShapContribution>();
        foreach (JsonElement item in root.GetProperty("top_shap").EnumerateArray())
        {
            topShap.Add(new ShapContribution(
                item.GetProperty("feature").GetString()!,
                item.GetProperty("value").GetDouble()));
        }
        return new ModelResult(risk, threshold, indicator, topShap);
    }
}