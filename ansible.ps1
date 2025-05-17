param (
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

# Commande Docker de base
$dockerCommand = "docker run -it --network ansible --add-host=host.docker.internal:host-gateway -v ${pwd}:/apps -w /apps ansible"

# Ajouter les arguments supplémentaires à la commande Docker
if ($Args) {
    $dockerCommand += " " + ($Args -join " ")
}

# Exécuter la commande Docker
Write-Host "Exécution de la commande Docker: $dockerCommand"
Invoke-Expression $dockerCommand