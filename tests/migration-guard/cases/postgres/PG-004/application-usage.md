# Application usage: `orders.customer_id` and `customers` deletion

## Reads

- `src/Repository/OrderRepository.php:34`
  ```php
  $qb->leftJoin('o.customer', 'c')->addSelect('c');
  ```
  → orders without a matching customer currently render an empty customer block; the UI tolerates
  `NULL` joins.

## Writes

- `src/Controller/OrderController.php:88` — creates an order with `customer_id` from the session;
  validated with `exists:customers,id` at the form layer.
- `src/Import/LegacyOrderImport.php:61` — inserts orders with `customer_id` from a CSV **without**
  checking that the customer exists.

## Deletion path (the thing the developer mentioned)

- `src/Controller/AdminCustomerController.php:120`
  ```php
  $entityManager->remove($customer);
  $entityManager->flush();
  ```
  → hard `DELETE FROM customers WHERE id = ?`. There is no cascade to `orders` and no `ON DELETE`
  behaviour implemented anywhere. With an FK using the default `NO ACTION`, this endpoint starts
  throwing `SQLSTATE[23503]` ("violates foreign key constraint") for every existing customer that has
  orders — which is almost all of them.

- `src/Command/AnonymiseCustomersCommand.php:44` — replaces customer PII; does not delete rows.

## Not searched

- The admin UI's error handling for the delete endpoint.
