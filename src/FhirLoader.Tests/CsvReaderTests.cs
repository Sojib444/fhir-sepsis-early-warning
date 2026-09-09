using FhirLoader;
using Xunit;

namespace FhirLoader.Tests;

public class CsvReaderTests
{
    [Fact]
    public void ParseLine_handles_plain_and_quoted_fields()
    {
        Assert.Equal(new[] { "a", "b", "c" }, CsvReader.ParseLine("a,b,c"));
        Assert.Equal(new[] { "a,b", "c" }, CsvReader.ParseLine("\"a,b\",c"));
        Assert.Equal(new[] { "he said \"hi\"", "" }, CsvReader.ParseLine("\"he said \"\"hi\"\"\","));
    }

    [Fact]
    public void Columns_map_by_name_and_missing_column_is_loud()
    {
        var map = CsvColumns.FromHeader(new[] { "patient_id", "site", "HR" });
        Assert.Equal(0, CsvColumns.Required(map, "patient_id"));
        Assert.Equal(2, CsvColumns.Required(map, "HR"));
        Assert.Throws<InvalidDataException>(() => CsvColumns.Required(map, "ICULOS"));
    }

    [Fact]
    public void Decimal_cell_empty_means_null()
    {
        Assert.Null(CsvColumns.Decimal(""));
        Assert.Equal(92.5m, CsvColumns.Decimal("92.5"));
    }

    [Fact]
    public void Round_trip_a_tiny_file()
    {
        var path = Path.Combine(Path.GetTempPath(), $"fhirloader-csv-{Guid.NewGuid():N}.csv");
        File.WriteAllText(path, "patient_id,site,hour\np1,A,0\np2,A,1\n");
        try
        {
            var rows = CsvReader.ReadAll(path).ToList();
            Assert.Equal(2, rows.Count);
            Assert.Equal("p1", rows[0][0]);
            Assert.Equal("1", rows[1][2]);
        }
        finally
        {
            File.Delete(path);
        }
    }
}