using System.Text.Json;

namespace CdsService;

/// <summary>
/// Loads the shared LOINC table (the same file FhirLoader uses) and answers
/// reverse lookups: LOINC -> study variable. Anything not in the table is not
/// one of the 34 clinical variables the model was trained on and is ignored.
/// </summary>
public sealed class LoincMap
{
    private readonly Dictionary<string, string> _byLoinc = new(StringComparer.Ordinal);

    public LoincMap(string jsonPath)
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(jsonPath));
        JsonElement variables = doc.RootElement.GetProperty("variables");
        foreach (JsonProperty prop in variables.EnumerateObject())
        {
            JsonElement entry = prop.Value;
            string loinc = entry.GetProperty("loinc").GetString()!;
            _byLoinc[loinc] = prop.Name;
        }
    }

    /// <summary>Study variable name for a LOINC code, or null if not tracked.</summary>
    public string? VariableForCode(string? loinc) =>
        loinc is not null && _byLoinc.TryGetValue(loinc, out var variable) ? variable : null;
}