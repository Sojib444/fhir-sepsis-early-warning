namespace CdsService.Tests;

public class LoincMapTests
{
    private readonly LoincMap _map = new(TestHelpers.LoincMapPath);

    [Fact]
    public void Reverse_lookup_finds_every_variable()
    {
        Assert.Equal("HR", _map.VariableForCode("8867-4"));
        Assert.Equal("Lactate", _map.VariableForCode("2524-7"));
        Assert.Equal("Platelets", _map.VariableForCode("777-3"));
    }

    [Fact]
    public void Unknown_code_returns_null()
    {
        Assert.Null(_map.VariableForCode("99999-9"));
        Assert.Null(_map.VariableForCode(null));
    }
}